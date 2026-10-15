"""Explicit concurrent-v1 composition; scenarios own data and handlers only."""

from typing import cast

from journeymap import __version__
from journeymap.application.concurrent import ConcurrentApplication
from journeymap.bootstrap import create_application, create_kernel
from journeymap.core.actions import WaitHandler
from journeymap.core.canonical import JsonObject, state_digest
from journeymap.core.concurrent import ConcurrentKernel
from journeymap.core.handlers import ActionRegistry, SystemEventRegistry
from journeymap.core.run import RunManifest
from journeymap.modules.inventory import InventoryModule
from journeymap.modules.knowledge import KnowledgeModule
from journeymap.modules.knowledge.records import KnowledgeRecord
from journeymap.modules.movement import MovementModule
from journeymap.modules.social import SocialModule
from journeymap.modules.trade import TradeModule
from journeymap.scenarios.phase1 import CloseRoute, initial_world


def create_phase1_kernel(*, state: JsonObject | None = None) -> ConcurrentKernel:
    movement, trade, social = MovementModule(), TradeModule(), SocialModule()
    actions, systems = ActionRegistry(), SystemEventRegistry()
    for module in (movement, trade, social):
        module.register_actions(actions)
    actions.register("WAIT", 1, WaitHandler())
    systems.register("CloseRoute", 1, CloseRoute())
    world = initial_world() if state is None else state
    return cast(
        ConcurrentKernel,
        create_kernel(
            initial_state=world,
            manifest=RunManifest(
                "run-phase1", "phase1", "concurrent-1", __version__, 1, 42, 0, state_digest(world)
            ),
            modules=(movement, trade, social, KnowledgeModule(), InventoryModule()),
            action_registry=actions,
            system_event_registry=systems,
            kernel_type=ConcurrentKernel,
        ),
    )


def create_phase1_application(
    kernel: ConcurrentKernel, *, patrol: bool = False
) -> ConcurrentApplication:
    instructions = (
        tuple(
            KnowledgeRecord(
                f"{kernel.manifest.run_id}:initial:{actor}:{place}",
                kernel.manifest.run_id,
                actor,
                place,
                "travel_route",
                {"route_id": route},
                "INITIAL",
                "phase1:patrol-instructions",
                0,
            )
            for actor, place, route in (
                ("A", "square", "a-route"),
                ("A", "a-end", "a-return"),
                ("B", "square", "b-route"),
                ("B", "b-end", "b-return"),
            )
        )
        if patrol
        else ()
    )
    authority, _ = create_application(kernel, social=True, initial_knowledge=instructions)
    return ConcurrentApplication(kernel, authority, social=True)
