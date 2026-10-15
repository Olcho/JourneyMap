"""Five-resident finite 7-day fixture and actor-scoped local perception."""

from dataclasses import dataclass

from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.entities import Entity, entity_state
from journeymap.core.observations import PerceptionContext
from journeymap.modules.inventory.models import ItemDefinition, inventory_state
from journeymap.modules.inventory.perception import perceive_inventory
from journeymap.modules.labor.models import Job
from journeymap.modules.labor.perception import perceive_jobs
from journeymap.modules.movement import ActorPosition, Location, Route, movement_state
from journeymap.modules.movement.perception import perceive_position
from journeymap.modules.survival.models import SurvivalState, survival_state
from journeymap.modules.survival.perception import perceive_survival
from journeymap.modules.trade.models import Offer, Wallet, trade_state
from journeymap.modules.trade.perception import perceive_trade

WORKERS = ("ada", "bert", "cora", "dara")
ACTORS = ("employer", *WORKERS)
DAY_TICKS = 24
SCENARIO_VERSION = "life-loop-1"


@dataclass(frozen=True, slots=True)
class LifeConfig:
    seed: int = 42
    horizon: int = 168
    employer_money: int = 240
    worker_money: int = 4
    flour: int = 160
    bread: int = 8
    wage: int = 10
    output_quantity: int = 8
    capacity: int = 2
    closure_tick: int | None = None

    def __post_init__(self) -> None:
        for name in ("seed", "horizon", "employer_money", "worker_money", "flour", "bread"):
            value = getattr(self, name)
            if type(value) is not int or (name != "seed" and value < 0):
                raise ValueError("invalid life config integer")
        if not 1 <= self.horizon <= 168:
            raise ValueError("life horizon must be 1..168")
        if any(
            type(v) is not int or v <= 0 for v in (self.wage, self.output_quantity, self.capacity)
        ):
            raise ValueError("invalid job config")
        if self.closure_tick is not None and (
            type(self.closure_tick) is not int or not 1 <= self.closure_tick <= self.horizon
        ):
            raise ValueError("invalid closure tick")


def routes() -> tuple[Route, ...]:
    return (
        Route("home-square", "homes", "square", 1),
        Route("square-home", "square", "homes", 1),
        Route("square-shop", "square", "workshop", 2),
        Route("shop-square", "workshop", "square", 2),
        Route("square-detour", "square", "detour", 1),
        Route("detour-square", "detour", "square", 1),
        Route("detour-shop", "detour", "workshop", 2),
        Route("shop-detour", "workshop", "detour", 2),
    )


def initial_world(config: LifeConfig) -> JsonObject:
    homes: JsonObject = {a: "workshop" if a == "employer" else "homes" for a in ACTORS}
    job = Job(
        "bake",
        "employer",
        "workshop",
        WORKERS,
        wage=config.wage,
        capacity=config.capacity,
        output_quantity=config.output_quantity,
    )
    survival = survival_state({a: SurvivalState(20, 10) for a in ACTORS}, {"bread": 10})
    survival["rest_locations"] = homes
    residents: JsonObject = {
        a: {
            "role": "employer" if a == "employer" else "worker",
            "home": homes[a],
            "market": "workshop",
            "job_location": "workshop",
            "job_id": "bake",
        }
        for a in ACTORS
    }
    return {
        "entities": entity_state(Entity(a, "person") for a in ACTORS),
        "movement": movement_state(
            locations=(Location(p) for p in ("homes", "square", "detour", "workshop")),
            routes=routes(),
            positions=(ActorPosition(a, str(homes[a])) for a in ACTORS),
        ),
        "inventory": inventory_state(
            (ItemDefinition("flour"), ItemDefinition("bread")),
            {
                a: {
                    "flour": config.flour if a == "employer" else 0,
                    "bread": config.bread if a == "employer" else 0,
                }
                for a in ACTORS
            },
        ),
        "trade": trade_state(
            (
                Wallet(a, config.employer_money if a == "employer" else config.worker_money)
                for a in ACTORS
            ),
            (Offer("bread-sale", "employer", "bread", 2),),
        ),
        "survival": survival,
        "labor": {"jobs": {job.job_id: job.to_json()}},
        "life": {"residents": residents},
    }


def perceive_life(world: JsonObject, actor: str) -> JsonObject:
    location = perceive_position(world, actor).get("location_id")
    movement = world["movement"]
    life = world["life"]
    assert isinstance(movement, dict) and isinstance(life, dict)
    all_routes = movement["routes"]
    residents = life["residents"]
    assert isinstance(all_routes, dict) and isinstance(residents, dict)
    exits: list[JsonValue] = [
        {key: r[key] for key in ("route_id", "origin", "destination", "traversal_cost", "passable")}
        for _, r in sorted(all_routes.items())
        if isinstance(r, dict) and r.get("origin") == location
    ]
    return {
        "inventory": perceive_inventory(world, actor),
        "survival": perceive_survival(world, actor),
        "trade": perceive_trade(world, actor),
        "life": {
            "resident": residents[actor],
            "local_routes": exits,
            "local_jobs": perceive_jobs(world, actor),
        },
    }


def contribute_life(context: PerceptionContext) -> JsonObject:
    return {"life": context.perceived["life"]}
