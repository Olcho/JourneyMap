"""Application composition root."""

from collections.abc import Iterable, Mapping
from pathlib import Path

from journeymap import __version__
from journeymap.adapters.sqlite import SQLitePersistence
from journeymap.application.observations import ObservationPipeline, contribute_self
from journeymap.application.perception import PerceptionExtension
from journeymap.application.research import ResearchView
from journeymap.application.session import SimulationApplication
from journeymap.core.canonical import JsonValue, clone_json_object, state_digest
from journeymap.core.events import EventBus
from journeymap.core.handlers import ActionRegistry, SystemEventRegistry
from journeymap.core.kernel import SimulationKernel
from journeymap.core.modules import Module, ModuleRegistry
from journeymap.core.run import RunManifest
from journeymap.modules.knowledge import KnowledgeLedger, KnowledgeRecord, contribute_knowledge
from journeymap.modules.knowledge.projection import KnowledgeProjector
from journeymap.modules.movement.perception import contribute_position


def create_alderwick_kernel(
    *,
    run_id: str = "run-alderwick",
    seed: int = 42,
    event_bus: EventBus | None = None,
    social: bool = False,
) -> SimulationKernel:
    """Unbooted scenario v1 kernel, also the unchanged ReplayHarness factory.

    Schedule installation remains explicit so replay never enqueues it twice.
    """
    from journeymap.core.actions import WaitHandler
    from journeymap.modules.knowledge import KnowledgeModule
    from journeymap.modules.movement import MovementModule
    from journeymap.modules.social import SocialModule
    from journeymap.scenarios.alderwick import AlderwickModule
    from journeymap.scenarios.alderwick.fixture import initial_world

    state = initial_world()
    movement, alderwick = MovementModule(), AlderwickModule()
    actions, systems = ActionRegistry(), SystemEventRegistry()
    movement.register_actions(actions)
    actions.register("WAIT", 1, WaitHandler())
    social_module = SocialModule()
    if social:
        social_module.register_actions(actions)
    alderwick.register_system_events(systems)
    return create_kernel(
        modules=(movement, KnowledgeModule(), alderwick, *((social_module,) if social else ())),
        initial_state=state,
        manifest=RunManifest(
            run_id, "alderwick", "1", __version__, 1, seed, 0, state_digest(state)
        ),
        action_registry=actions,
        system_event_registry=systems,
        event_bus=event_bus,
    )


def create_alderwick_application(
    kernel: SimulationKernel,
    *,
    social: bool = False,
) -> tuple[SimulationApplication, ResearchView]:
    """Explicit trusted composition; generic M3 composition remains unchanged."""
    from journeymap.modules.social.perception import contribute_social
    from journeymap.scenarios.alderwick.bridge import perceive_bridge
    from journeymap.scenarios.alderwick.contributors import contribute_bridge
    from journeymap.scenarios.alderwick.fixture import initial_knowledge
    from journeymap.scenarios.alderwick.knowledge import project_bridge_knowledge
    from journeymap.scenarios.alderwick.social import social_initial_knowledge

    if (kernel.manifest.scenario_id, kernel.manifest.scenario_version) != ("alderwick", "1"):
        raise ValueError("Alderwick application requires scenario v1")
    if social and "social" not in kernel.module_ids:
        raise ValueError("social application requires the social module")
    pipeline = ObservationPipeline()
    pipeline.register(
        priority=0, module_id="core", contributor_id="self", contributor=contribute_self
    )
    pipeline.register(
        priority=10,
        module_id="movement",
        contributor_id="position",
        contributor=contribute_position,
    )
    pipeline.register(
        priority=15, module_id="alderwick", contributor_id="bridge", contributor=contribute_bridge
    )
    pipeline.register(
        priority=20,
        module_id="knowledge",
        contributor_id="records",
        contributor=contribute_knowledge,
    )
    if social:
        pipeline.register(
            priority=30,
            module_id="social",
            contributor_id="interactions",
            contributor=contribute_social,
        )
    return create_application(
        kernel,
        initial_knowledge=(
            social_initial_knowledge(kernel.manifest.run_id)
            if social
            else initial_knowledge(kernel.manifest.run_id)
        ),
        pipeline=pipeline,
        knowledge_projector=project_bridge_knowledge,
        perception_extension=perceive_bridge,
        social=social,
    )


def create_kernel(
    *,
    database: str | Path = ":memory:",
    modules: Iterable[Module] = (),
    manifest: RunManifest | None = None,
    initial_state: Mapping[str, JsonValue] | None = None,
    action_registry: ActionRegistry | None = None,
    system_event_registry: SystemEventRegistry | None = None,
    event_bus: EventBus | None = None,
) -> SimulationKernel:
    """Compose an unbooted kernel from explicit in-process dependencies."""

    state = clone_json_object(initial_state or {})
    if manifest is None:
        manifest = RunManifest(
            run_id="run-default",
            scenario_id="empty",
            scenario_version="1",
            engine_version=__version__,
            schema_version=1,
            seed=0,
            start_time=0,
            initial_state_digest=state_digest(state),
        )
    registry = ModuleRegistry()
    for module in modules:
        registry.register(module)
    return SimulationKernel(
        modules=registry,
        persistence=SQLitePersistence(database),
        manifest=manifest,
        initial_state=state,
        action_registry=action_registry or ActionRegistry(),
        system_event_registry=system_event_registry or SystemEventRegistry(),
        event_bus=event_bus or EventBus(),
    )


def create_application(
    kernel: SimulationKernel,
    *,
    initial_knowledge: Iterable[KnowledgeRecord] = (),
    pipeline: ObservationPipeline | None = None,
    knowledge_projector: KnowledgeProjector | None = None,
    perception_extension: PerceptionExtension | None = None,
    social: bool = False,
) -> tuple[SimulationApplication, ResearchView]:
    """Compose once per run; give Controllers only application.game_for(actor).

    The caller retains kernel lifecycle/scenario authority and the separate
    research view. A supplied pipeline replaces the explicit default assembly.
    """
    knowledge = KnowledgeLedger(
        kernel.manifest.run_id, kernel.manifest.start_time, initial_knowledge
    )
    if pipeline is None:
        pipeline = ObservationPipeline()
        pipeline.register(
            priority=0, module_id="core", contributor_id="self", contributor=contribute_self
        )
        pipeline.register(
            priority=10,
            module_id="movement",
            contributor_id="position",
            contributor=contribute_position,
        )
        pipeline.register(
            priority=20,
            module_id="knowledge",
            contributor_id="records",
            contributor=contribute_knowledge,
        )
        if social:
            from journeymap.modules.social.perception import contribute_social

            pipeline.register(
                priority=30,
                module_id="social",
                contributor_id="interactions",
                contributor=contribute_social,
            )
    application = SimulationApplication(
        kernel,
        knowledge,
        pipeline,
        knowledge_projector=knowledge_projector,
        perception_extension=perception_extension,
        social=social,
    )
    return application, ResearchView(kernel, application, knowledge)
