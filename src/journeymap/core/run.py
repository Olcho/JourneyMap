"""Simulation run identity, logical time, and deterministic randomness."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Self

UINT64_LIMIT = 1 << 64
UINT64_MASK = UINT64_LIMIT - 1
SPLITMIX64_GAMMA = 0x9E3779B97F4A7C15


class ClockError(ValueError):
    """Raised when logical time would move backwards."""


class SimulationClock:
    """A wall-clock-independent logical clock measured in integer ticks."""

    def __init__(self, start_time: int = 0) -> None:
        if not isinstance(start_time, int) or isinstance(start_time, bool) or start_time < 0:
            raise ClockError("start_time must be a non-negative integer")
        self._now = start_time

    @property
    def now(self) -> int:
        return self._now

    def advance_to(self, target_time: int) -> None:
        if not isinstance(target_time, int) or isinstance(target_time, bool):
            raise ClockError("target_time must be an integer")
        if target_time < self._now:
            raise ClockError(f"cannot move time backwards from {self._now} to {target_time}")
        self._now = target_time

    def advance_by(self, ticks: int) -> None:
        if not isinstance(ticks, int) or isinstance(ticks, bool) or ticks < 0:
            raise ClockError("ticks must be a non-negative integer")
        self._now += ticks

    def _restore(self, logical_time: int) -> None:
        """Restore a captured tick after an uncommitted transition fails."""

        if not isinstance(logical_time, int) or isinstance(logical_time, bool) or logical_time < 0:
            raise ClockError("restored logical time must be a non-negative integer")
        self._now = logical_time


@dataclass(frozen=True, slots=True)
class RngSnapshot:
    """Serializable state for a deterministic RNG transaction."""

    state: int
    draw_count: int


class DeterministicRng:
    """SplitMix64 RNG with an explicit algorithm and tracked draw count."""

    def __init__(self, seed: int) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise TypeError("seed must be an integer")
        self._state = seed & UINT64_MASK
        self._draw_count = 0

    @property
    def draw_count(self) -> int:
        return self._draw_count

    def next_u64(self) -> int:
        self._state = (self._state + SPLITMIX64_GAMMA) & UINT64_MASK
        value = self._state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & UINT64_MASK
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & UINT64_MASK
        self._draw_count += 1
        return (value ^ (value >> 31)) & UINT64_MASK

    def randbelow(self, stop: int) -> int:
        """Draw uniformly from ``range(stop)`` without modulo bias."""

        if not isinstance(stop, int) or isinstance(stop, bool):
            raise TypeError("stop must be an integer")
        if stop <= 0 or stop > UINT64_LIMIT:
            raise ValueError("stop must be in the range 1..2**64")
        rejection_threshold = UINT64_LIMIT % stop
        while True:
            value = self.next_u64()
            if value >= rejection_threshold:
                return value % stop

    def snapshot(self) -> RngSnapshot:
        return RngSnapshot(self._state, self._draw_count)

    def restore(self, snapshot: RngSnapshot) -> None:
        if not 0 <= snapshot.state <= UINT64_MASK or snapshot.draw_count < 0:
            raise ValueError("invalid RNG snapshot")
        self._state = snapshot.state
        self._draw_count = snapshot.draw_count

    def clone(self) -> Self:
        clone = type(self)(0)
        clone.restore(self.snapshot())
        return clone


class RunStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class RunManifest:
    """Immutable inputs that identify a reproducible simulation run."""

    run_id: str
    scenario_id: str
    scenario_version: str
    engine_version: str
    schema_version: int
    seed: int
    start_time: int
    initial_state_digest: str

    def __post_init__(self) -> None:
        text_fields = (
            self.run_id,
            self.scenario_id,
            self.scenario_version,
            self.engine_version,
            self.initial_state_digest,
        )
        if any(not value for value in text_fields):
            raise ValueError("run manifest text fields must not be empty")
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version <= 0
        ):
            raise ValueError("schema_version must be a positive integer")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise TypeError("seed must be an integer")
        if (
            not isinstance(self.start_time, int)
            or isinstance(self.start_time, bool)
            or self.start_time < 0
        ):
            raise ValueError("start_time must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class TransitionIdentity:
    """Deterministic identity and provenance for one committed transition."""

    transition_id: str
    run_id: str
    sequence: int
    simulation_time: int
    source_kind: str
    source_ref: str
    causation_id: str
    correlation_id: str


class SimulationRun:
    """Mutable lifecycle and deterministic services for one run manifest."""

    def __init__(self, manifest: RunManifest) -> None:
        self.manifest = manifest
        self.clock = SimulationClock(manifest.start_time)
        self.rng = DeterministicRng(manifest.seed)
        self._status = RunStatus.CREATED
        self._transition_sequence = 0
        self._event_sequence = 0

    @property
    def status(self) -> RunStatus:
        return self._status

    @property
    def transition_sequence(self) -> int:
        return self._transition_sequence

    @property
    def event_sequence(self) -> int:
        return self._event_sequence

    def start(self) -> None:
        if self._status is not RunStatus.CREATED:
            raise RuntimeError(f"cannot start run in status {self._status}")
        self._status = RunStatus.RUNNING

    def close(self) -> None:
        if self._status is RunStatus.RUNNING:
            self._status = RunStatus.CLOSED

    def pending_transition(
        self,
        *,
        source_kind: str,
        source_ref: str,
        causation_id: str,
        correlation_id: str,
    ) -> TransitionIdentity:
        """Build the next identity without consuming its sequence."""

        self._ensure_running()
        sequence = self._transition_sequence + 1
        return TransitionIdentity(
            transition_id=f"{self.manifest.run_id}:transition:{sequence:08d}",
            run_id=self.manifest.run_id,
            sequence=sequence,
            simulation_time=self.clock.now,
            source_kind=source_kind,
            source_ref=source_ref,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )

    def pending_event_identity(self, offset: int) -> tuple[str, int]:
        """Build an event identity relative to the last committed sequence."""

        self._ensure_running()
        if offset <= 0:
            raise ValueError("event sequence offset must be positive")
        sequence = self._event_sequence + offset
        return (
            f"{self.manifest.run_id}:event:{sequence:08d}",
            sequence,
        )

    def commit_sequences(self, event_count: int) -> None:
        """Consume one transition sequence and its prepared event sequences."""

        self._ensure_running()
        if event_count < 0:
            raise ValueError("event_count must be non-negative")
        self._transition_sequence += 1
        self._event_sequence += event_count

    def _ensure_running(self) -> None:
        if self._status is not RunStatus.RUNNING:
            raise RuntimeError("run must be running to prepare or commit identities")
