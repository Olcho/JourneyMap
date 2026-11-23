"""Versioned, local LifeSession checkpoint. No pickle, imports, or command replay.

This trusted composition adapter owns private runtime hydration. Controllers and
GamePorts never receive it. Only paused, successful driver boundaries are supported;
an interrupted handler/subscriber operation must be repaired before saving.
"""

import hashlib
import json
import os
import tempfile
from dataclasses import fields, is_dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from types import UnionType
from typing import Any, TypeAliasType, Union, get_args, get_origin, get_type_hints

from journeymap.adapters.life import LifeDecision
from journeymap.adapters.life_llm import LifeLLMConfig
from journeymap.adapters.llm import Record
from journeymap.adapters.provider import ProviderRequest, RawModelResponse
from journeymap.application.concurrent import ConcurrentAttempt, ConcurrentReceipt
from journeymap.application.concurrent_replay import LiveInput
from journeymap.application.contracts import request_fingerprint
from journeymap.core.canonical import canonical_json
from journeymap.core.concurrent import Admission, ExecutionCommand, ExecutionRecord, InFlight
from journeymap.core.events import EventEnvelope, EventSourceKind
from journeymap.core.handlers import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    ActionTiming,
    SystemEventOutcome,
)
from journeymap.core.observations import Observation, validate_observation_v1
from journeymap.core.run import RngSnapshot, RunManifest, RunStatus, TransitionIdentity
from journeymap.core.scheduler import ScheduledEvent, ScheduledEventSpec
from journeymap.experiments.life import LifeSession
from journeymap.experiments.life_decisions import LifeCall, LifeLLMState
from journeymap.experiments.mixed_life import MixedLifeSession
from journeymap.modules.knowledge.records import KnowledgeRecord
from journeymap.scenarios.life import ACTORS, LifeConfig

FORMAT = "journeymap-life-checkpoint-2"
# Explicit allowlist, never import a class named by an input file.
RECORDS: dict[str, Any] = {
    cls.__name__: cls
    for cls in (
        LifeLLMConfig,
        LifeCall,
        LifeLLMState,
        Record,
        ProviderRequest,
        RawModelResponse,
        LifeConfig,
        LifeDecision,
        ConcurrentAttempt,
        ConcurrentReceipt,
        LiveInput,
        Admission,
        ExecutionCommand,
        ExecutionRecord,
        InFlight,
        EventEnvelope,
        ActionRequest,
        ActionResult,
        ActionTiming,
        SystemEventOutcome,
        RngSnapshot,
        RunManifest,
        TransitionIdentity,
        ScheduledEvent,
        ScheduledEventSpec,
        KnowledgeRecord,
    )
}
ENUMS: dict[str, Any] = {c.__name__: c for c in (EventSourceKind, ActionStatus, RunStatus)}


@lru_cache(maxsize=32)
def _hints(cls: type[Any]) -> dict[str, Any]:
    return get_type_hints(cls)


def _matches(value: Any, annotation: Any) -> bool:
    if isinstance(annotation, TypeAliasType):
        return _matches(value, annotation.__value__)
    origin, arguments = get_origin(annotation), get_args(annotation)
    if origin in (Union, UnionType):
        return any(_matches(value, a) for a in arguments)
    if origin is dict:
        return type(value) is dict and all(
            _matches(k, arguments[0]) and _matches(v, arguments[1]) for k, v in value.items()
        )
    if origin in (list, set, tuple):
        if type(value) is not origin:
            return False
        if origin is tuple and len(arguments) > 1 and arguments[-1] is not Ellipsis:
            return len(value) == len(arguments) and all(
                _matches(v, a) for v, a in zip(value, arguments, strict=True)
            )
        return all(_matches(v, arguments[0]) for v in value)
    if annotation is str:
        return isinstance(value, str)
    return type(value) is annotation


def _compatibility(session: LifeSession) -> dict[str, Any]:
    # Fail closed across source changes; this v1 has no migration mechanism.
    package = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return {
        "source_sha256": digest.hexdigest(),
        "modules": {
            name: module.metadata.version
            for name, module in session.kernel._modules._modules.items()
        },
    }


# These fields are data only. Services, subscribers, contributors, persistence
# connections and bound callbacks are always constructed by the trusted factory.
FIELDS = {
    "session": (
        "inputs",
        "observations",
        "daily",
        "risk_ticks",
        "_risk_samples",
        "max_hunger",
        "max_fatigue",
        "_cursor",
        "_sampled",
    ),
    "kernel": (
        "_state",
        "_events",
        "_action_results",
        "_system_event_outcomes",
        "_in_flight",
        "_admissions",
        "_journal",
        "_commands",
        "_priority_cursor",
        "_completion_batch",
    ),
    "run": ("manifest", "_status", "_transition_sequence", "_event_sequence"),
    "clock": ("_now",),
    "scheduler": ("_run_id", "_next_insertion_sequence", "_items"),
    "application": ("_requests", "_attempts", "_intents"),
    "authority": ("_traces", "_requests"),
    "history": ("_run_id", "_records", "_by_id"),
    "knowledge": ("_records", "run_id", "initial_time"),
}
POLICY_FIELDS = ("_blocked", "_seen_receipt", "_buy_retry_at", "_work_retry_at", "_decisions")


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return {"enum": type(value).__name__, "value": value.value}
    if type(value) in (str, int, bool, float) or value is None:
        return value
    if isinstance(value, Observation):
        return {
            "observation": {
                k: getattr(value, k)
                for k in (
                    "run_id",
                    "actor_id",
                    "observation_sequence",
                    "simulation_time",
                    "schema_version",
                    "content",
                )
            }
        }
    if is_dataclass(value) and not isinstance(value, type):
        if type(value).__name__ not in RECORDS:
            raise ValueError("unsupported checkpoint record")
        return {
            "record": type(value).__name__,
            "fields": {f.name: _encode(getattr(value, f.name)) for f in fields(value)},
        }
    if type(value) is dict:
        return {"dict": [[_encode(k), _encode(v)] for k, v in value.items()]}
    if type(value) in (list, tuple, set):
        items = sorted(value) if type(value) is set else value
        return {type(value).__name__: [_encode(v) for v in items]}
    raise ValueError("unsupported checkpoint value")


def _decode(value: Any) -> Any:
    if type(value) in (str, int, bool, float) or value is None:
        return value
    if type(value) is not dict:
        raise ValueError("invalid checkpoint encoding")
    keys = set(value)
    if keys == {"enum", "value"} and value["enum"] in ENUMS:
        return ENUMS[value["enum"]](value["value"])
    if keys == {"observation"}:
        record = Observation(**value["observation"])
        validate_observation_v1(record)
        return record
    if keys == {"record", "fields"} and value["record"] in RECORDS:
        cls = RECORDS[value["record"]]
        if set(value["fields"]) != {f.name for f in fields(cls)}:
            raise ValueError("checkpoint record fields mismatch")
        data = {k: _decode(v) for k, v in value["fields"].items()}
        hints = _hints(cls)
        if any(not _matches(v, hints[k]) for k, v in data.items()):
            raise ValueError("checkpoint record type mismatch")
        return cls(**data)
    if len(keys) == 1:
        kind = next(iter(keys))
        items = value[kind]
        if type(items) is not list:
            raise ValueError("invalid checkpoint container")
        if kind == "dict":
            pairs = [(_decode(k), _decode(v)) for k, v in items]
            result = dict(pairs)
            if len(result) != len(pairs):
                raise ValueError("duplicate checkpoint key")
            return result
        if kind in ("list", "tuple", "set"):
            decoded = [_decode(v) for v in items]
            return {"list": list, "tuple": tuple, "set": set}[kind](decoded)
    raise ValueError("unknown checkpoint encoding")


def _objects(session: LifeSession) -> dict[str, Any]:
    kernel = session.kernel
    authority = session.application._authority
    return {
        "session": session,
        "kernel": kernel,
        "run": kernel._run,
        "clock": kernel._run.clock,
        "scheduler": kernel._scheduler,
        "application": session.application,
        "authority": authority,
        "history": authority._observations,
        "knowledge": authority._knowledge,
    }


def _safe_boundary(session: LifeSession) -> None:
    if isinstance(session, MixedLifeSession):
        session.decider.checkpoint_ready()
    app = session.application
    if (
        not session.kernel.is_booted
        or session.kernel._transition_active
        or app._busy
        or app._authority._busy
        or app._authority._observations._building
        or session.kernel._completion_batch
        or any(a is None for _, a in session.kernel._admissions.values())
        or any(r is None for _, _, r in app._requests.values())
    ):
        raise ValueError("checkpoint requires a quiescent successful driver boundary")


def snapshot(session: LifeSession) -> dict[str, Any]:
    """Detached complete continuation state, including append positions/identities."""
    _safe_boundary(session)
    return {
        "compatibility": _compatibility(session),
        "controller": None
        if not isinstance(session, MixedLifeSession)
        else {
            "configuration": _encode(session.llm_config),
            "state": _encode(session.decider.state),
            "activation": _encode(session._activation),
        },
        "config": _encode(session.config),
        "objects": {
            name: {key: _encode(getattr(obj, key)) for key in FIELDS[name]}
            for name, obj in _objects(session).items()
        },
        "policies": {
            actor: {key: _encode(getattr(policy, key)) for key in POLICY_FIELDS}
            for actor, policy in session.controllers.items()
        },
        "rng": _encode(session.kernel.rng_snapshot),
    }


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def save_checkpoint(session: LifeSession, path: Path) -> None:
    """Atomic replace; an incomplete write never replaces the last checkpoint."""
    payload = snapshot(session)
    document = {"format": FORMAT, "sha256": _digest(payload), "payload": payload}
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _validate(session: LifeSession, expected_manifest: RunManifest) -> None:
    """Validate continuation indices and references before exposing a restored session."""
    k, app = session.kernel, session.application
    now = k.simulation_time
    if k.manifest != expected_manifest or not 0 <= now <= session.config.horizon:
        raise ValueError("checkpoint identity or tick mismatch")
    if set(session.controllers) != set(ACTORS) or k._run._status is not RunStatus.RUNNING:
        raise ValueError("checkpoint lifecycle mismatch")
    # Validate canonical JSON and the scenario's balance/need readers.
    from journeymap.experiments.life import people

    people(k.state_snapshot)
    if k._run._event_sequence != len(k.events):
        raise ValueError("checkpoint event cursor mismatch")
    transitions = [o.transition for o in k.system_event_outcomes] + [
        r.transition for r in k.action_results if r.transition is not None
    ]
    if sorted(t.sequence for t in transitions) != list(range(1, k.transition_sequence + 1)):
        raise ValueError("checkpoint transition cursor mismatch")
    for index, event in enumerate(k.events, 1):
        if (
            event.event_sequence != index
            or event.event_id != f"{k.manifest.run_id}:event:{index:08d}"
            or event.run_id != k.manifest.run_id
            or event.simulation_time > now
        ):
            raise ValueError("checkpoint event identity mismatch")
    history = app._authority._observations
    if history._run_id != k.manifest.run_id or session.observations != history._records:
        raise ValueError("checkpoint observation archive mismatch")
    if history._by_id != {o.observation_id: o for o in history._records}:
        raise ValueError("checkpoint observation index mismatch")
    for index, obs in enumerate(history._records, 1):
        if (
            obs.observation_sequence != index
            or obs.run_id != k.manifest.run_id
            or obs.actor_id not in ACTORS
            or obs.simulation_time > now
        ):
            raise ValueError("checkpoint observation identity mismatch")
    if [a.sequence for a in app.attempts] != list(range(1, len(app.attempts) + 1)):
        raise ValueError("checkpoint attempt cursor mismatch")
    if [r.sequence for r in k.journal] != list(range(1, len(k.journal) + 1)):
        raise ValueError("checkpoint journal cursor mismatch")
    for rid, (actor, fingerprint, receipt) in app._requests.items():
        request = app._intents[rid]
        if (
            actor != request.actor_id
            or fingerprint != request_fingerprint(request)
            or receipt is None
            or receipt.action_request_id != rid
        ):
            raise ValueError("checkpoint application cache mismatch")
    completed = {r.action_request_id for r in k.action_results}
    if len(completed) != len(k.action_results):
        raise ValueError("duplicate checkpoint completion")
    for actor, flight in k._in_flight.items():
        rid = flight.request.action_request_id
        handler = k._action_registry.get(flight.request.action_type, flight.request.schema_version)
        if (
            actor not in ACTORS
            or flight.request.actor_id != actor
            or rid in completed
            or flight.started_at > now
            or flight.completes_at <= now
            or handler.handler_id != flight.handler_id
            or (
                flight.timing is not None
                and flight.completes_at != flight.started_at + flight.timing.duration
            )
        ):
            raise ValueError("invalid checkpoint pending action")
        if k._admissions[rid][1] is None or rid not in app._requests:
            raise ValueError("missing checkpoint pending cache")
    scheduler = k._scheduler
    if scheduler._run_id != k.manifest.run_id:
        raise ValueError("checkpoint scheduler run mismatch")
    insertions = set()
    for key, scheduled in scheduler._items:
        if (
            key != scheduled.ordering_key
            or scheduled.due_time <= now
            or scheduled.run_id != scheduler._run_id
            or scheduled.insertion_sequence >= scheduler._next_insertion_sequence
            or scheduled.insertion_sequence in insertions
        ):
            raise ValueError("invalid checkpoint schedule")
        insertions.add(scheduled.insertion_sequence)
        k._system_event_registry.get(scheduled.event_type, scheduled.schema_version)
    import heapq

    heapq.heapify(scheduler._items)
    if (
        session._cursor < 0
        or k._priority_cursor < 0
        or any(t > now for t in session._sampled)
        or (now > 0 and now not in session._sampled)
    ):
        raise ValueError("checkpoint driver cursor mismatch")
    for actor in ACTORS:
        if session.risk_ticks[actor] != len(session._risk_samples[actor]):
            raise ValueError("checkpoint accounting cursor mismatch")
    _safe_boundary(session)


def load_checkpoint(path: Path) -> LifeSession:
    """Build trusted services and hydrate data directly, without preparing/resolving actions."""
    session = None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if (
            set(document) != {"format", "sha256", "payload"}
            or document["format"] != FORMAT
            or _digest(document["payload"]) != document["sha256"]
        ):
            raise ValueError("unsupported or damaged checkpoint")
        payload = document["payload"]
        if set(payload) != {"config", "objects", "policies", "rng", "compatibility", "controller"}:
            raise ValueError("invalid checkpoint sections")
        config = _decode(payload["config"])
        if type(config) is not LifeConfig:
            raise ValueError("invalid checkpoint config")
        controller = payload["controller"]
        if controller is None:
            session = LifeSession(config, _restoring=True)
        else:
            if set(controller) != {"configuration", "state", "activation"}:
                raise ValueError("invalid checkpoint controller fields")
            configuration = _decode(controller["configuration"])
            state = _decode(controller["state"])
            activation = _decode(controller["activation"])
            if (
                type(configuration) is not LifeLLMConfig
                or type(state) is not LifeLLMState
                or not _matches(activation, list[str] | None)
            ):
                raise ValueError("invalid checkpoint controller state")
            session = MixedLifeSession(config, configuration, _restoring=True)
            session.decider.state = state
            session._activation = activation
            if (
                state.status not in {"IDLE", "READY", "PAUSED_ERROR", "LIMIT_REACHED"}
                or state.requests_started != sum(c.sent for c in state.calls)
                or state.fixture_position != state.requests_started
                or not 0 <= state.requests_started <= configuration.request_limit
                or state.fixture_position > len(configuration.responses)
                or [c.sequence for c in state.calls] != list(range(1, len(state.calls) + 1))
                or (
                    activation is not None
                    and (
                        len(set(activation)) != len(activation)
                        or not set(activation) <= set(ACTORS)
                    )
                )
                or (
                    state.status == "READY"
                    and (
                        state.ready is None
                        or state.observation is None
                        or not activation
                        or activation[0] != configuration.actor
                    )
                )
            ):
                raise ValueError("checkpoint decision cursor mismatch")
        if payload["compatibility"] != _compatibility(session):
            raise ValueError("checkpoint source/module compatibility mismatch")
        expected_manifest = session.kernel.manifest
        objects = _objects(session)
        if set(payload["objects"]) != set(objects) or set(payload["policies"]) != set(ACTORS):
            raise ValueError("checkpoint composition mismatch")
        for name, obj in objects.items():
            data = payload["objects"][name]
            if set(data) != set(FIELDS[name]):
                raise ValueError("checkpoint runtime fields mismatch")
            for key, value in data.items():
                decoded = _decode(value)
                if type(decoded) is not type(getattr(obj, key)):
                    raise ValueError("checkpoint runtime type mismatch")
                setattr(obj, key, decoded)
        for actor, policy in session.controllers.items():
            data = payload["policies"][actor]
            if set(data) != set(POLICY_FIELDS):
                raise ValueError("checkpoint policy fields mismatch")
            for key, value in data.items():
                decoded = _decode(value)
                annotation = {
                    "_blocked": set[str],
                    "_seen_receipt": str | None,
                    "_buy_retry_at": int,
                    "_work_retry_at": int,
                    "_decisions": list[LifeDecision],
                }[key]
                if not _matches(decoded, annotation):
                    raise ValueError("checkpoint policy type mismatch")
                setattr(policy, key, decoded)
        rng = _decode(payload["rng"])
        if type(rng) is not RngSnapshot:
            raise ValueError("invalid checkpoint RNG")
        session.kernel._run.rng.restore(rng)
        _validate(session, expected_manifest)
        return session
    except (ValueError, TypeError, LookupError, AttributeError) as error:
        if session is not None:
            session.kernel.close()
        raise ValueError("invalid LifeSession checkpoint") from error


def checkpoint_digest(session: LifeSession) -> str:
    return _digest(snapshot(session))
