"""Application composition root."""

from collections.abc import Iterable, Mapping
from pathlib import Path

from journeymap import __version__
from journeymap.adapters.sqlite import SQLitePersistence
from journeymap.application.observations import ObservationPipeline, contribute_self
from journeymap.application.research import ResearchView
from journeymap.application.session import SimulationApplication
from journeymap.core.canonical import JsonValue, clone_json_object, state_digest
from journeymap.core.events import EventBus
from journeymap.core.handlers import ActionRegistry, SystemEventRegistry
from journeymap.core.kernel import SimulationKernel
from journeymap.core.modules import Module, ModuleRegistry
from journeymap.core.run import RunManifest
from journeymap.modules.knowledge import KnowledgeLedger, KnowledgeRecord, contribute_knowledge
from journeymap.modules.movement.perception import contribute_position


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
    application = SimulationApplication(kernel, knowledge, pipeline)
    return application, ResearchView(kernel, application, knowledge)
