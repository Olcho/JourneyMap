"""Explicit finite M6 author inputs; M4/M5 fixtures remain independent."""

from journeymap.core.canonical import JsonObject
from journeymap.core.scheduler import ScenarioSchedule, ScheduledEventSpec
from journeymap.modules.inventory.models import ItemDefinition, inventory_state
from journeymap.modules.inventory.perception import perceive_inventory
from journeymap.modules.survival.models import SurvivalState, survival_state
from journeymap.modules.survival.perception import perceive_survival
from journeymap.modules.trade.models import Offer, Wallet, trade_state
from journeymap.modules.trade.perception import perceive_trade
from journeymap.scenarios.alderwick.bridge import perceive_bridge
from journeymap.scenarios.alderwick.fixture import ACTOR_LOCATIONS, initial_world, scenario_schedule


def resource_world() -> JsonObject:
    world = initial_world()
    world["inventory"] = inventory_state(
        (ItemDefinition("bread"),),
        {actor: {"bread": 5 if actor == "edwin" else 0} for actor, _ in ACTOR_LOCATIONS},
    )
    world["survival"] = survival_state(
        {
            actor: SurvivalState(20, 10) if actor == "stranger" else SurvivalState(0, 0)
            for actor, _ in ACTOR_LOCATIONS
        },
        {"bread": 10},
    )
    world["trade"] = trade_state(
        (Wallet(actor, 10 if actor == "stranger" else 0) for actor, _ in ACTOR_LOCATIONS),
        (Offer("edwin-bread", "edwin", "bread", 2),),
    )
    return world


def resource_schedule() -> ScenarioSchedule:
    """Tick 1..20 only. Advancing past this horizon does not invent more ticks."""
    return ScenarioSchedule(
        "alderwick",
        "resources-1",
        (
            *scenario_schedule().events,
            *(ScheduledEventSpec(tick, 10, "SurvivalTick", 1, {}) for tick in range(1, 21)),
        ),
    )


def perceive_resources(world: JsonObject, actor_id: str) -> JsonObject:
    return {
        **perceive_bridge(world, actor_id),
        "inventory": perceive_inventory(world, actor_id),
        "survival": perceive_survival(world, actor_id),
        "trade": perceive_trade(world, actor_id),
    }
