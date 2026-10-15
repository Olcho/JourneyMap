"""Single-owner local live controls and side-effect-free observation inspection."""

from dataclasses import asdict
from pathlib import Path
from typing import Any

from journeymap.application.perception import perceive
from journeymap.experiments.checkpoint import load_checkpoint, save_checkpoint
from journeymap.experiments.life import LifeSession, people
from journeymap.experiments.mixed_life import MixedLifeSession
from journeymap.scenarios.life import ACTORS


class LiveLife:
    """Called only by the HTTP owner thread; LifeSession alone advances logical time."""

    def __init__(self, session: LifeSession, checkpoint: Path) -> None:
        self.session = session
        self.checkpoint = checkpoint
        self.running = False
        self.speed = 1.0
        self._deadline = 0.0
        self._step_target: int | None = None
        self.message = "일시 정지 · 시작 또는 한 시간 진행을 누르세요."

    @property
    def at_horizon(self) -> bool:
        return self.session.kernel.simulation_time >= self.session.config.horizon

    def command(self, data: dict[str, Any], now: float) -> None:
        command = data.get("command")
        if command == "start":
            self.running = not self.at_horizon
            self._deadline = now + 1 / self.speed
            if self.running:
                self.message = "생활 순환 진행 중 · 정지하거나 저장할 수 있습니다."
        elif command == "pause":
            self.running = False
            self._step_target = None
            self.message = "일시 정지 · 현재 상태를 관찰하거나 저장하세요."
        elif command == "step":
            self.running = False
            self.message = "한 시간 진행 완료 · 일시 정지"
            self._step_target = self.session.kernel.simulation_time + 1
            self._step(now)
        elif command == "speed":
            speed = data.get("value")
            if type(speed) not in (int, float) or speed not in (0.5, 1, 4, 16):
                raise ValueError("unsupported speed")
            self.speed = float(speed)
            self._deadline = now + 1 / self.speed
        elif command == "save":
            self.running = False
            self._step_target = None
            if isinstance(self.session, MixedLifeSession):
                self.session.decider.poll()
            save_checkpoint(self.session, self.checkpoint)
            self.message = f"체크포인트 저장 완료 · {self.checkpoint.name}"
        elif command == "restore":
            self.running = False
            self._step_target = None
            if isinstance(self.session, MixedLifeSession):
                self.session.decider.checkpoint_ready()
            restored = load_checkpoint(self.checkpoint)
            old = self.session
            self.session = restored
            old.close()
            self.message = "상태 직접 복원 완료 · 일시 정지"
        elif command == "release_response" and isinstance(self.session, MixedLifeSession):
            self.session.decider.release_response()
        else:
            raise ValueError("unknown control command")

    def _step(self, now: float | None = None) -> None:
        if not self.at_horizon:
            target = self._step_target or self.session.kernel.simulation_time + 1
            if isinstance(self.session, MixedLifeSession):
                from time import monotonic

                if not self.session.progress(target, monotonic() if now is None else now):
                    if self.session.decider.state.status in {"PAUSED_ERROR", "LIMIT_REACHED"}:
                        self.running = False
                        self._step_target = None
                    return
            else:
                self.session.run_until(target)
        self._step_target = None
        if self.at_horizon:
            self.running = False
            self.message = "HORIZON 도달 · 예약된 미완료 행동은 체크포인트에 보존됩니다."

    def pump(self, now: float) -> None:
        # Never catch up by changing simulated duration or skipping intermediate
        # ticks. A slow host simply provides lower actual throughput.
        if isinstance(self.session, MixedLifeSession):
            self.session.decider.poll()
        if (self.running or self._step_target is not None) and now >= self._deadline:
            self._step(now)
            self._deadline = now + 1 / self.speed

    def view(self, actor: str | None = None) -> dict[str, Any]:
        session, kernel = self.session, self.session.kernel
        if actor is not None and actor not in ACTORS:
            raise ValueError("unknown actor")
        base: dict[str, Any] = {
            "mode": "LIVE_SESSION",
            "scope": "researcher" if actor is None else "actor",
            "actor": actor,
            "tick": kernel.simulation_time,
            "horizon": session.config.horizon,
            "status": "HORIZON" if self.at_horizon else "RUNNING" if self.running else "PAUSED",
            "speed": self.speed,
            "message": self.message,
        }
        base["controllers"] = {a: "RULE" for a in ACTORS}
        if isinstance(session, MixedLifeSession):
            base["controllers"][session.llm_config.actor] = "LLM_ADAPTER_OFFLINE"
            state = session.decider.state
            if state.status in {"WAITING_FOR_DECISION", "PAUSED_ERROR", "LIMIT_REACHED"}:
                base["status"] = state.status
            elif state.status == "READY" and not self.running and self._step_target is None:
                base["status"] = "DECISION_READY_PAUSED"
            base["run_requested"] = self.running or self._step_target is not None
            base["provider"] = {
                "hold_responses": session.llm_config.hold_responses,
                "actor": session.llm_config.actor,
                "fixture": session.llm_config.fixture_version,
                "requests_started": state.requests_started,
                "request_limit": session.llm_config.request_limit,
                "memory": "현재 Observation만 사용; RULE의 blocked/retry memory와 다름",
                "calls": [asdict(c) for c in state.calls[-20:]]
                if actor is None or actor == session.llm_config.actor
                else [],
            }
        flights = {f.request.actor_id: f for f in kernel.in_flight}
        if actor is None:
            world = kernel.state_snapshot
            base.update(
                {
                    "people": people(world),
                    "movement": world["movement"],
                    "actions": {a: asdict(f) for a, f in flights.items()},
                    "decisions": [asdict(d) for d in session.decisions[-20:]],
                    "events": [asdict(e) for e in kernel.events[-60:]],
                    "counts": {
                        "events": len(kernel.events),
                        "observations": len(session.observations),
                        "attempts": len(session.application.attempts),
                    },
                }
            )
        else:
            authority = session.application._authority
            context = perceive(
                run_id=kernel.manifest.run_id,
                actor_id=actor,
                simulation_time=kernel.simulation_time,
                world=kernel.perception_snapshot,
                knowledge=authority.knowledge_snapshot(),
                extension=authority._perception_extension,
            )
            preview = authority._pipeline.assemble(context)
            delivered = next(
                (o for o in reversed(session.observations) if o.actor_id == actor), None
            )
            base.update(
                {
                    "preview": preview,
                    "delivered": None
                    if delivered is None
                    else {
                        "id": delivered.observation_id,
                        "tick": delivered.simulation_time,
                        "content": delivered.content,
                    },
                    "action": asdict(flights[actor]) if actor in flights else None,
                    "decisions": [asdict(d) for d in session.controllers[actor].decisions[-20:]],
                    "receipts": [
                        asdict(a.receipt)
                        for a in session.application.attempts
                        if a.request.actor_id == actor and a.receipt is not None
                    ][-60:],
                }
            )
        return base
