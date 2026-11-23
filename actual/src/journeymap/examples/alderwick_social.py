"""M5: python -m journeymap.examples.alderwick_social."""

from journeymap.adapters.social_npc import SocialNpcController
from journeymap.bootstrap import create_alderwick_application, create_alderwick_kernel
from journeymap.core.replay import ReplayHarness, ReplayInput
from journeymap.modules.knowledge import KnowledgeLedger
from journeymap.modules.knowledge.projection import KnowledgeProjection
from journeymap.modules.social.knowledge import project_informed_knowledge
from journeymap.scenarios.alderwick.fixture import scenario_schedule
from journeymap.scenarios.alderwick.knowledge import project_bridge_knowledge
from journeymap.scenarios.alderwick.social import NPC_ACTIVATIONS, social_initial_knowledge


def main() -> None:
    kernel = create_alderwick_kernel(social=True)
    kernel.boot()
    try:
        schedule = scenario_schedule()
        for event in schedule.events:
            kernel.schedule(event)
        application, research = create_alderwick_application(kernel, social=True)
        kernel.advance_to(3)
        controller = SocialNpcController()
        for actor in NPC_ACTIVATIONS:
            game = application.game_for(actor)
            observation = game.observe()
            request = controller.decide(observation)
            result = game.submit(request)
            print(
                f"{observation.simulation_time}: {actor} {request.action_type} -> {result.status}"
            )
        replay = ReplayHarness(lambda: create_alderwick_kernel(social=True)).run(
            ReplayInput(
                schedule,
                tuple(trace.request for trace in research.action_traces),
                kernel.simulation_time,
            )
        )
        assert replay.action_results == research.action_results
        assert replay.events == research.events
        assert replay.final_state == research.world_snapshot
        projected = KnowledgeProjection(
            KnowledgeLedger(
                kernel.manifest.run_id, 0, social_initial_knowledge(kernel.manifest.run_id)
            ),
            replay.events,
            project_bridge_knowledge,
            event_rules=(project_informed_knowledge,),
        )
        assert projected.history() == application.knowledge_snapshot().history()
        for record in research.knowledge_history("thomas"):
            if record.predicate == "condition":
                print(f"Thomas: {record.value} ({record.source_kind}, {record.source_ref})")
        print(
            f"Replay and Knowledge match; tick={kernel.simulation_time}; "
            f"digest={replay.final_state_digest}"
        )
    finally:
        kernel.close()


if __name__ == "__main__":
    main()
