"""Headless application orchestration and explicit accounting for Phase 2."""

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import cast

from journeymap.adapters.life import LifeController, LifeDecision
from journeymap.application.concurrent import ConcurrentApplication
from journeymap.application.concurrent_replay import LiveInput, replay_live_inputs
from journeymap.bootstrap_life import create_life_application, create_life_kernel
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.concurrent import ConcurrentKernel
from journeymap.core.concurrent_replay import replay_concurrent
from journeymap.core.events import EventEnvelope
from journeymap.core.observations import Observation
from journeymap.core.scheduler import ScheduledEventSpec
from journeymap.modules.inventory.models import owned_quantities
from journeymap.modules.survival.models import actor_survival
from journeymap.modules.survival.schedule import needs_schedule
from journeymap.modules.trade.models import wallet_balance
from journeymap.scenarios.life import ACTORS, DAY_TICKS, LifeConfig, initial_world


def json_data(value: object) -> JsonObject:
    data = json.loads(json.dumps(value, ensure_ascii=False))
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    return cast(JsonObject, data)


def people(world: JsonObject) -> JsonObject:
    return {
        actor: {
            "wallet": wallet_balance(world, actor),
            "inventory": json_data(owned_quantities(world, actor)),
            **actor_survival(world, actor).to_json(),
        }
        for actor in ACTORS
    }


def integer(value: JsonValue) -> int:
    if type(value) is not int:
        raise ValueError("expected integer in engine record")
    return value


def resource_flows(initial: JsonObject, events: tuple[EventEnvelope, ...]) -> list[JsonValue]:
    """Fold declared transfers/conversions into readable balances at actual ticks."""
    wallets = {a: wallet_balance(initial, a) for a in ACTORS}
    inventory = {a: owned_quantities(initial, a) for a in ACTORS}
    needs = {a: actor_survival(initial, a).to_json() for a in ACTORS}
    rows: list[JsonValue] = []
    for event in events:
        p = event.payload
        kind = event.event_type
        actor = p.get("actor_id")
        if kind == "SurvivalAdvanced":
            assert isinstance(actor, str) and isinstance(p["after"], dict)
            needs[actor] = p["after"]
            continue
        if kind == "WorkCompleted":
            actor, employer = str(p["worker_id"]), str(p["employer_id"])
            inventory[employer][str(p["input_item"])] -= integer(p["input_quantity"])
            inventory[employer][str(p["output_item"])] += integer(p["output_quantity"])
            wallets[employer] -= integer(p["wage"])
            wallets[actor] += integer(p["wage"])
            needs[actor]["fatigue"] = p["fatigue_after"]
        elif kind == "ItemPurchased":
            actor, seller = str(p["buyer_id"]), str(p["seller_id"])
            inventory[seller][str(p["item_id"])] -= integer(p["quantity"])
            inventory[actor][str(p["item_id"])] += integer(p["quantity"])
            wallets[seller] += integer(p["total_price"])
            wallets[actor] -= integer(p["total_price"])
        elif kind == "ItemConsumed":
            assert isinstance(actor, str)
            inventory[actor][str(p["item_id"])] -= integer(p["quantity"])
            needs[actor]["hunger"] = p["hunger_after"]
        elif kind == "ActorRested":
            assert isinstance(actor, str)
            needs[actor]["fatigue"] = p["fatigue_after"]
        else:
            continue
        assert isinstance(actor, str)
        rows.append(
            {
                "tick": event.simulation_time,
                "event_id": event.event_id,
                "request_id": event.source_ref,
                "actor": actor,
                "kind": kind,
                "wallet_after": wallets[actor],
                "bread_after": inventory[actor]["bread"],
                "hunger_after": needs[actor]["hunger"],
                "fatigue_after": needs[actor]["fatigue"],
                "employer_wallet_after": wallets["employer"],
                "employer_flour_after": inventory["employer"]["flour"],
                "employer_bread_after": inventory["employer"]["bread"],
                "effect": p,
            }
        )
    return rows


class LifeSession:
    """Trusted driver; policies receive only their own bound Observation."""

    def __init__(self, config: LifeConfig, *, _restoring: bool = False) -> None:
        self.config = config
        self.kernel = create_life_kernel(config)
        self.kernel.boot()
        self.application = create_life_application(self.kernel)
        self.controllers = {a: LifeController() for a in ACTORS}
        self.inputs: list[LiveInput] = []
        self.observations: list[Observation] = []
        self.daily: list[JsonObject] = []
        self.risk_ticks = {a: 0 for a in ACTORS}
        self._risk_samples: dict[str, set[int]] = {a: set() for a in ACTORS}
        self.max_hunger = {a: 20 for a in ACTORS}
        self.max_fatigue = {a: 10 for a in ACTORS}
        self._cursor = 0
        self._sampled: set[int] = set()
        if _restoring:
            return
        for spec in needs_schedule(0, config.horizon):
            self.install(spec)
        if config.closure_tick is not None:
            self.install(
                ScheduledEventSpec(
                    config.closure_tick, 0, "CloseRoute", 1, {"route_id": "square-shop"}
                )
            )

    def install(self, spec: ScheduledEventSpec) -> None:
        self.kernel.schedule(spec)
        self.inputs.append(LiveInput("schedule", self.kernel.simulation_time, schedule=spec))

    def advance(self, tick: int) -> None:
        self.kernel.advance_to(tick)
        self.inputs.append(LiveInput("advance", tick))

    def run_until(self, horizon: int) -> None:
        if (
            type(horizon) is not int
            or not self.kernel.simulation_time <= horizon <= self.config.horizon
        ):
            raise ValueError("invalid life run horizon")
        for tick in range(self.kernel.simulation_time, horizon + 1):
            self._sample_tick(tick)
            if tick == horizon:
                break
            actors = sorted(self.controllers)
            pivot = self._cursor % len(actors)
            self._cursor += 1
            busy = {f.request.actor_id for f in self.kernel.in_flight}
            for actor in actors[pivot:] + actors[:pivot]:
                if actor in busy:
                    continue
                game = self.application.game_for(actor)
                observation = game.observe()
                self.observations.append(observation)
                self.inputs.append(
                    LiveInput(
                        "observe",
                        tick,
                        actor,
                        observation_id=observation.observation_id,
                        observation_digest=observation.content_digest,
                    )
                )
                request = self.controllers[actor].decide(observation)
                receipt = game.submit(request)
                self.inputs.append(
                    LiveInput("submit", tick, actor, request=request, receipt=receipt)
                )
                self.advance(tick)

    def _sample_tick(self, tick: int) -> None:
        # A stopped boundary has already advanced and sampled this tick.
        # Re-enter it without appending a second advance record.
        if tick not in self._sampled:
            self.advance(tick)
        if tick not in self._sampled:
            self._sampled.add(tick)
            world = self.kernel.state_snapshot
            for actor in ACTORS:
                need = actor_survival(world, actor)
                self.max_hunger[actor] = max(self.max_hunger[actor], need.hunger)
                self.max_fatigue[actor] = max(self.max_fatigue[actor], need.fatigue)
                if tick < self.config.horizon and (need.hunger >= 90 or need.fatigue >= 80):
                    self.risk_ticks[actor] += 1
                    self._risk_samples[actor].add(tick)
            if tick % DAY_TICKS == 0 or tick == self.config.horizon:
                self.daily.append({"tick": tick, "people": people(world)})

    @property
    def decisions(self) -> tuple[LifeDecision, ...]:
        return tuple(
            sorted(
                (d for c in self.controllers.values() for d in c.decisions),
                key=lambda d: int(d.request_id.split(":observation:")[1].split(":")[0]),
            )
        )

    def report(self) -> JsonObject:
        kernel = self.kernel
        initial, final = initial_world(self.config), kernel.state_snapshot
        events = kernel.events
        production = [e for e in events if e.event_type == "WorkCompleted"]
        consumed = sum(
            integer(e.payload["quantity"]) for e in events if e.event_type == "ItemConsumed"
        )
        material_used = sum(integer(e.payload["input_quantity"]) for e in production)
        produced = sum(integer(e.payload["output_quantity"]) for e in production)
        money_initial = sum(wallet_balance(initial, a) for a in ACTORS)
        money_final = sum(wallet_balance(final, a) for a in ACTORS)
        flour_final = sum(owned_quantities(final, a).get("flour", 0) for a in ACTORS)
        bread_final = sum(owned_quantities(final, a).get("bread", 0) for a in ACTORS)
        actors: JsonObject = {}
        by_request = {a.request.action_request_id: a.request for a in self.application.attempts}
        daily: list[JsonValue] = []
        previous_tick = -1
        for snapshot in self.daily:
            end = integer(snapshot["tick"])
            activity: JsonObject = {}
            for actor in ACTORS:
                completed = [
                    r
                    for r in kernel.action_results
                    if previous_tick < r.resolved_at <= end
                    and by_request[r.action_request_id].actor_id == actor
                ]
                activity[actor] = {
                    "success": dict(
                        Counter(
                            by_request[r.action_request_id].action_type
                            for r in completed
                            if r.status == "SUCCEEDED"
                        )
                    ),
                    "failures": dict(
                        Counter(
                            r.reason_code or "NONE" for r in completed if r.status != "SUCCEEDED"
                        )
                    ),
                }
            daily.append({**snapshot, "activities": activity})
            previous_tick = end
        flows = resource_flows(initial, events)
        ada_flows = [r for r in flows if isinstance(r, dict) and r["actor"] == "ada"]
        cycles: list[JsonValue] = []
        stages = ("WorkCompleted", "ItemPurchased", "ItemConsumed", "ActorRested")
        stage, begin = 0, 0
        for i, row in enumerate(ada_flows):
            if row["kind"] != stages[stage]:
                continue
            if stage == 0:
                begin = i
            stage += 1
            if stage == len(stages):
                cycles.append(cast(list[JsonValue], ada_flows[begin : i + 1]))
                stage = 0
                if len(cycles) == 2:
                    break
        for actor in ACTORS:
            successes = Counter(
                by_request[r.action_request_id].action_type
                for r in kernel.action_results
                if r.status == "SUCCEEDED" and by_request[r.action_request_id].actor_id == actor
            )
            failures = Counter(
                r.reason_code or "NONE"
                for r in kernel.action_results
                if r.status != "SUCCEEDED" and by_request[r.action_request_id].actor_id == actor
            )
            wages = sum(
                integer(e.payload["wage"]) for e in production if e.payload["worker_id"] == actor
            )
            intervals: list[JsonValue] = []
            samples = sorted(self._risk_samples[actor])
            if samples:
                begin_risk = end_risk = samples[0]
                for sample in samples[1:]:
                    if sample != end_risk + 1:
                        intervals.append({"start": begin_risk, "end_exclusive": end_risk + 1})
                        begin_risk = sample
                    end_risk = sample
                intervals.append({"start": begin_risk, "end_exclusive": end_risk + 1})
            actors[actor] = {
                "successful_actions": dict(successes),
                "wages_received": wages,
                "failures": dict(failures),
                "risk_ticks": self.risk_ticks[actor],
                "risk_intervals": intervals,
                "max_hunger": self.max_hunger[actor],
                "max_fatigue": self.max_fatigue[actor],
            }
        checks: JsonObject = {
            "currency": money_initial == money_final,
            "flour": self.config.flour - material_used == flour_final,
            "bread": self.config.bread + produced - consumed == bread_final,
        }
        return {
            "schema": "phase2-life-1",
            "config": json_data(asdict(self.config)),
            "time_unit": "1 tick = 1 simulated hour",
            "day_ticks": DAY_TICKS,
            "final_tick": kernel.simulation_time,
            "actors": actors,
            "final_people": people(final),
            "daily": daily,
            "resource_flows": flows,
            "example_worker": "ada",
            "example_life_cycles": cycles,
            "accounting": {
                "currency_initial": money_initial,
                "currency_final": money_final,
                "flour_initial": self.config.flour,
                "flour_used": material_used,
                "flour_final": flour_final,
                "bread_initial": self.config.bread,
                "bread_produced": produced,
                "bread_consumed": consumed,
                "bread_final": bread_final,
                "wages_transferred": sum(integer(e.payload["wage"]) for e in production),
                "checks": checks,
            },
            "event_count": len(events),
            "decision_count": len(self.decisions),
            "pending": [json_data(asdict(f)) for f in kernel.in_flight],
            "state_digest": kernel.state_digest,
        }

    def close(self) -> None:
        self.kernel.close()


@dataclass(frozen=True, slots=True)
class LifeRun:
    summary: JsonObject
    decisions: tuple[LifeDecision, ...]
    inputs: tuple[LiveInput, ...]
    events: tuple[JsonObject, ...]
    results: tuple[JsonObject, ...]
    attempts: tuple[JsonObject, ...]
    execution: JsonObject


def run_life(config: LifeConfig | None = None, *, verify_replay: bool = False) -> LifeRun:
    config = LifeConfig() if config is None else config
    start = perf_counter()
    session = LifeSession(config)
    try:
        session.run_until(config.horizon)
        simulation_elapsed = perf_counter() - start
        summary = session.report()
        final = session.kernel.execution_snapshot
        checks: JsonObject = {"engine_replay": None, "live_attempt_observation_replay": None}
        if verify_replay:
            engine = replay_concurrent(
                lambda: create_life_kernel(config), session.kernel.commands, capture_frames=False
            )

            def factory() -> tuple[ConcurrentKernel, ConcurrentApplication]:
                kernel = create_life_kernel(config)
                return kernel, create_life_application(kernel)

            live = replay_live_inputs(factory, tuple(session.inputs))
            checks = {
                "engine_replay": engine.final_execution == final,
                "live_attempt_observation_replay": live.final_execution == final
                and live.attempts == session.application.attempts
                and live.observations == tuple(session.observations),
            }
            if not all(checks.values()):
                raise AssertionError("life replay mismatch")
        summary["reproduction"] = checks
        summary["simulation_elapsed_seconds"] = simulation_elapsed
        summary["elapsed_seconds"] = perf_counter() - start
        return LifeRun(
            summary,
            session.decisions,
            tuple(session.inputs),
            tuple(json_data(asdict(e)) for e in session.kernel.events),
            tuple(json_data(asdict(r)) for r in session.kernel.action_results),
            tuple(json_data(asdict(a)) for a in session.application.attempts),
            final,
        )
    finally:
        session.close()


def export_life(run: LifeRun, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "summary.json").write_text(
        json.dumps(run.summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (directory / "execution.json").write_text(
        json.dumps(run.execution, ensure_ascii=False), encoding="utf-8"
    )
    for name, entries in (
        ("decisions", tuple(json_data(asdict(d)) for d in run.decisions)),
        ("inputs", tuple(json_data(asdict(i)) for i in run.inputs)),
        ("events", run.events),
        ("results", run.results),
        ("attempts", run.attempts),
    ):
        (directory / f"{name}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in entries), encoding="utf-8"
        )


def readable_summary(run: LifeRun) -> str:
    s = run.summary
    lines = [
        f"Phase 2: {s['final_tick']} hours / {s['event_count']} events / "
        f"simulation {s['simulation_elapsed_seconds']:.3f}s / total {s['elapsed_seconds']:.3f}s"
    ]
    for actor, row in cast(JsonObject, s["actors"]).items():
        r = cast(JsonObject, row)
        lines.append(
            f"{actor}: actions={r['successful_actions']}, wages={r['wages_received']}, "
            f"failures={r['failures']}, risk_ticks={r['risk_ticks']}"
        )
    lines.append("Accounting: " + json.dumps(s["accounting"], ensure_ascii=False))
    for day in cast(list[JsonObject], s["daily"]):
        lines.append(f"hour {day['tick']}: " + json.dumps(day["people"], ensure_ascii=False))
        lines.append("  activities: " + json.dumps(day["activities"], ensure_ascii=False))
    lines.append(
        "Actual example cycles (ada): " + json.dumps(s["example_life_cycles"], ensure_ascii=False)
    )
    lines.append("Replay: " + json.dumps(s["reproduction"]))
    return "\n".join(lines)
