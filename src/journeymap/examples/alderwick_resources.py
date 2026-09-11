"""M6: python -m journeymap.examples.alderwick_resources."""

from journeymap.bootstrap import create_alderwick_application, create_alderwick_kernel
from journeymap.core.canonical import JsonObject
from journeymap.core.handlers import ActionRequest, ActionStatus
from journeymap.core.replay import ReplayHarness, ReplayInput
from journeymap.scenarios.alderwick.resources import resource_schedule

# Trusted example instructions, submitted through the actor-bound GamePort.
RESOURCE_PATH: tuple[tuple[str, JsonObject], ...] = (
    ("WAIT", {"duration": 1}),
    ("MOVE", {"route_id": "west-gate-to-village-square"}),
    ("MOVE", {"route_id": "village-square-to-bakery"}),
    ("BUY", {"offer_id": "edwin-bread", "quantity": 2}),
    ("CONSUME", {"item_id": "bread", "quantity": 1}),
    ("REST", {"duration": 3}),
)


def main() -> None:
    kernel = create_alderwick_kernel(resources=True)
    kernel.boot()
    try:
        schedule = resource_schedule()
        for event in schedule.events:
            kernel.schedule(event)
        app, research = create_alderwick_application(kernel, resources=True)
        game = app.game_for("stranger")
        for action, payload in RESOURCE_PATH:
            observation = game.observe()
            request = ActionRequest(
                f"{observation.observation_id}:{action}",
                observation.run_id,
                observation.actor_id,
                observation.observation_id,
                observation.simulation_time,
                action,
                1,
                payload,
            )
            result = game.submit(request)
            assert result.status == ActionStatus.SUCCEEDED
            print(f"{result.started_at}->{result.resolved_at}: {action} {result.status}")
        print(game.observe().content)
        report = ReplayHarness(lambda: create_alderwick_kernel(resources=True)).run(
            ReplayInput(
                schedule,
                tuple(trace.request for trace in research.action_traces),
                kernel.simulation_time,
            )
        )
        assert report.action_results == kernel.action_results
        assert report.system_event_outcomes == kernel.system_event_outcomes
        assert report.events == kernel.events
        assert report.final_state == kernel.state_snapshot
        assert report.final_state_digest == kernel.state_digest
        assert report.final_simulation_time == kernel.simulation_time
        assert report.rng_draw_count == kernel.rng_snapshot.draw_count
        print(f"Replay matches; tick={kernel.simulation_time}; digest={kernel.state_digest}")
    finally:
        kernel.close()


if __name__ == "__main__":
    main()
