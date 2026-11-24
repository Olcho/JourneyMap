"""Explicit opt-in composition; no Phase 1 or research scenario reinterpretation."""

from typing import cast

from journeymap import __version__
from journeymap.application.concurrent import ConcurrentApplication
from journeymap.application.observations import ObservationPipeline, contribute_last_receipt
from journeymap.bootstrap import create_application, create_kernel
from journeymap.core.actions import WaitHandler
from journeymap.core.canonical import JsonObject, state_digest
from journeymap.core.concurrent import ConcurrentKernel
from journeymap.core.handlers import ActionRegistry, SystemEventRegistry
from journeymap.core.run import RunManifest
from journeymap.modules.inventory import InventoryModule
from journeymap.modules.knowledge import KnowledgeModule
from journeymap.modules.knowledge.records import KnowledgeRecord, contribute_knowledge
from journeymap.modules.labor import LaborModule
from journeymap.modules.labor.rest import HomeRestHandler
from journeymap.modules.movement import MovementModule
from journeymap.modules.movement.perception import contribute_position
from journeymap.modules.survival import SurvivalModule
from journeymap.modules.survival.handlers import ConsumeHandler
from journeymap.modules.trade import TradeModule
from journeymap.scenarios.alderwick.resource_contributors import (
    contribute_inventory,
    contribute_survival,
    contribute_trade,
)
from journeymap.scenarios.life import (
    ACTORS,
    SCENARIO_VERSION,
    LifeConfig,
    contribute_life,
    initial_world,
    perceive_life,
    routes,
)
from journeymap.scenarios.phase1 import CloseRoute


def create_life_kernel(config: LifeConfig, *, state: JsonObject | None = None) -> ConcurrentKernel:
    world = initial_world(config) if state is None else state
    movement, trade, labor, survival = (
        MovementModule(),
        TradeModule(),
        LaborModule(),
        SurvivalModule(),
    )
    actions, systems = ActionRegistry(), SystemEventRegistry()
    for module in (movement, trade, labor):
        module.register_actions(actions)
    actions.register("WAIT", 1, WaitHandler())
    actions.register("REST", 1, HomeRestHandler())
    actions.register("CONSUME", 1, ConsumeHandler())
    survival.register_system_events(systems)
    systems.register("CloseRoute", 1, CloseRoute())
    return cast(
        ConcurrentKernel,
        create_kernel(
            initial_state=world,
            manifest=RunManifest(
                "run-life",
                "life",
                SCENARIO_VERSION,
                __version__,
                1,
                config.seed,
                0,
                state_digest(world),
            ),
            modules=(movement, trade, labor, survival, InventoryModule(), KnowledgeModule()),
            action_registry=actions,
            system_event_registry=systems,
            kernel_type=ConcurrentKernel,
        ),
    )


def create_life_application(kernel: ConcurrentKernel) -> ConcurrentApplication:
    from journeymap.application.observations import contribute_self

    pipeline = ObservationPipeline()
    for priority, module, name, contributor in (
        (0, "core", "self", contribute_self),
        (10, "movement", "position", contribute_position),
        (20, "knowledge", "records", contribute_knowledge),
        (30, "life", "local", contribute_life),
        (40, "inventory", "resources", contribute_inventory),
        (40, "survival", "resources", contribute_survival),
        (40, "trade", "resources", contribute_trade),
        (50, "application", "last_receipt", contribute_last_receipt),
    ):
        pipeline.register(
            priority=priority, module_id=module, contributor_id=name, contributor=contributor
        )
    holder: list[ConcurrentApplication] = []

    def extension(world: JsonObject, actor: str) -> JsonObject:
        return {**perceive_life(world, actor), "last_action": holder[0].latest_public_result(actor)}

    initial = tuple(
        KnowledgeRecord(
            f"{kernel.manifest.run_id}:initial:map:{actor}",
            kernel.manifest.run_id,
            actor,
            "settlement",
            "known_routes",
            [
                {
                    key: row.to_json()[key]
                    for key in ("route_id", "origin", "destination", "traversal_cost")
                }
                for row in routes()
            ],
            "INITIAL",
            "life:initial-settlement-map",
            0,
        )
        for actor in ACTORS
    )
    authority, _ = create_application(
        kernel, pipeline=pipeline, perception_extension=extension, initial_knowledge=initial
    )
    application = ConcurrentApplication(kernel, authority)
    holder.append(application)
    return application
