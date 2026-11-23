"""Run with python -m journeymap.examples.alderwick; no external services."""

from journeymap.adapters.scripted import ScriptedController
from journeymap.bootstrap import create_alderwick_application, create_alderwick_kernel
from journeymap.core.replay import ReplayHarness, ReplayInput
from journeymap.scenarios.alderwick.fixture import scenario_schedule


def main() -> None:
    kernel = create_alderwick_kernel()
    kernel.boot()
    try:
        schedule = scenario_schedule()
        for event in schedule.events:
            kernel.schedule(event)
        application, research = create_alderwick_application(kernel)
        game, controller = application.game_for("stranger"), ScriptedController()
        for _ in range(3):
            observation = game.observe()
            request = controller.decide(observation)
            result = game.submit(request)
            print(f"{observation.simulation_time}: {request.action_type} -> {result.status}")
        game.observe()
        replay = ReplayHarness(create_alderwick_kernel).run(
            ReplayInput(
                schedule,
                tuple(trace.request for trace in research.action_traces),
                kernel.simulation_time,
            )
        )
        assert replay.events == research.events
        assert replay.final_state == research.world_snapshot
        print(f"Knowledge records: {len(research.knowledge_history('stranger'))}")
        print(f"Replay digest: {replay.final_state_digest}")
    finally:
        kernel.close()


if __name__ == "__main__":
    main()
