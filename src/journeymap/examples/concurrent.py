"""Offline real-engine examples: python -m journeymap.examples.concurrent."""

import json
from dataclasses import asdict

from journeymap.adapters.scripted import ScriptedController
from journeymap.application.concurrent import AutonomousRunner, ConcurrentGamePort
from journeymap.bootstrap_phase1 import create_phase1_application, create_phase1_kernel
from journeymap.core.canonical import JsonObject
from journeymap.core.handlers import ActionRequest
from journeymap.core.scheduler import ScheduledEventSpec


def intent(game: ConcurrentGamePort, action: str, payload: JsonObject) -> ActionRequest:
    observation = game.observe()
    return ActionRequest(
        f"{observation.observation_id}:intent",
        observation.run_id,
        observation.actor_id,
        observation.observation_id,
        observation.simulation_time,
        action,
        1,
        payload,
    )


def scene(*, closure: int | None = None, buy: bool = False) -> dict[str, object]:
    kernel = create_phase1_kernel()
    kernel.boot()
    try:
        app = create_phase1_application(kernel)
        if closure is not None:
            kernel.schedule(
                ScheduledEventSpec(closure, 0, "CloseRoute", 1, {"route_id": "a-route"})
            )
        admissions = []
        for actor, route in (("A", "a-route"), ("B", "b-route")):
            game = app.game_for(actor)
            request = intent(
                game,
                "BUY" if buy else "MOVE",
                {"offer_id": "bread-offer", "quantity": 1} if buy else {"route_id": route},
            )
            admissions.append(asdict(game.submit(request)))
        accepted_tick = kernel.simulation_time
        kernel.advance_to(1)
        midway = [asdict(f) for f in kernel.in_flight]
        kernel.advance_to(2)
        return {
            "accepted_tick": accepted_tick,
            "admissions": admissions,
            "in_flight_at_1": midway,
            "final_tick": kernel.simulation_time,
            "results": [asdict(r) for r in kernel.action_results],
            "world": kernel.state_snapshot,
            "journal": [asdict(r) for r in kernel.journal],
        }
    finally:
        kernel.close()


def main() -> None:
    print(
        json.dumps(
            {
                "normal": scene(),
                "closure_at_1": scene(closure=1),
                "closure_at_2": scene(closure=2),
                "stock_race": scene(buy=True),
                "autonomous_patrol": patrol_scene(),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def patrol_scene() -> dict[str, object]:
    kernel = create_phase1_kernel()
    kernel.boot()
    try:
        app = create_phase1_application(kernel, patrol=True)
        runner = AutonomousRunner(
            kernel, app, {"A": ScriptedController(), "B": ScriptedController()}
        )
        runner.run_until(6)
        return {
            "activations": [asdict(a) for a in runner.activations],
            "results": [asdict(r) for r in kernel.action_results],
            "final_tick": kernel.simulation_time,
            "in_flight": [asdict(f) for f in kernel.in_flight],
        }
    finally:
        kernel.close()


if __name__ == "__main__":
    main()
