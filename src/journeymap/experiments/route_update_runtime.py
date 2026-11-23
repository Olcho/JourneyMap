"""E04 trusted runtime assembly outside the declarative scenario layer."""

from journeymap import __version__
from journeymap.application.observations import ObservationPipeline, contribute_self
from journeymap.application.research import ResearchView
from journeymap.application.session import SimulationApplication
from journeymap.bootstrap import create_application, create_kernel
from journeymap.core.actions import WaitHandler
from journeymap.core.canonical import state_digest
from journeymap.core.handlers import (
    ActionRegistry,
    SystemEventRegistry,
)
from journeymap.core.kernel import SimulationKernel
from journeymap.core.run import RunManifest
from journeymap.modules.knowledge import KnowledgeModule, contribute_knowledge
from journeymap.modules.movement import (
    MovementModule,
)
from journeymap.modules.movement.perception import contribute_position
from journeymap.scenarios.route_update import (
    VERSION,
    CloseHandler,
    NoticeHandler,
    RouteUpdateModule,
    contribute_local,
    initial_knowledge,
    perceive_local,
    project_knowledge,
    world,
)


def create_runtime(
    run_id: str, seed: int = 42
) -> tuple[SimulationKernel, SimulationApplication, ResearchView]:
    state, movement = world(), MovementModule()
    actions, systems = ActionRegistry(), SystemEventRegistry()
    movement.register_actions(actions)
    actions.register("WAIT", 1, WaitHandler())
    systems.register("CLOSE_ROUTES", 1, CloseHandler())
    systems.register("OFFICIAL_NOTICE", 1, NoticeHandler())
    kernel = create_kernel(
        modules=(movement, KnowledgeModule(), RouteUpdateModule()),
        initial_state=state,
        manifest=RunManifest(
            run_id, "route-update", VERSION, __version__, 1, seed, 0, state_digest(state)
        ),
        action_registry=actions,
        system_event_registry=systems,
    )
    kernel.boot()
    pipeline = ObservationPipeline()
    for priority, module, identity, contributor in (
        (0, "core", "self", contribute_self),
        (10, "movement", "position", contribute_position),
        (16, "route-update", "local", contribute_local),
        (20, "knowledge", "records", contribute_knowledge),
    ):
        pipeline.register(
            priority=priority, module_id=module, contributor_id=identity, contributor=contributor
        )
    app, research = create_application(
        kernel,
        initial_knowledge=initial_knowledge(run_id),
        pipeline=pipeline,
        perception_extension=perceive_local,
        knowledge_projector=project_knowledge,
        include_last_receipt=False,
    )
    return kernel, app, research
