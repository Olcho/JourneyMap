"""E04 researcher-only optimality/arrival metrics, separate from code policies."""

from typing import cast

from journeymap.adapters.route_update import obj, rows
from journeymap.adapters.route_update_policy import shortest_moves
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.scenarios.route_update import ACTOR


def truth_baseline(world: JsonObject, position: str) -> JsonObject:
    routes = obj(obj(world["movement"])["routes"])
    graph = [
        {
            "route_id": r["route_id"],
            "origin": r["origin"],
            "destination": r["destination"],
            "cost": r["traversal_cost"],
            "passable": r["passable"],
        }
        for r in map(obj, routes.values())
    ]
    return shortest_moves(graph, position)


def arrived(step: JsonObject, report: JsonObject) -> bool:
    request, receipt = step.get("request"), step.get("receipt")
    if not isinstance(request, dict) or not isinstance(receipt, dict):
        return False
    results = [
        r
        for r in rows(report["action_results"])
        if r["action_request_id"] == request["action_request_id"]
    ]
    position = obj(obj(obj(report["final_state"])["movement"])["positions"])
    return (
        step["engine_submitted"] is True
        and request["action_type"] == "MOVE"
        and receipt["status"] == "SUCCEEDED"
        and len(results) == 1
        and results[0]["status"] == "SUCCEEDED"
        and obj(position[ACTOR])["location_id"] == "G"
        and any(
            e["event_type"] == "ActorMoved"
            and e["source_ref"] == request["action_request_id"]
            and obj(e["payload"])["destination"] == "G"
            for e in rows(report["events"])
        )
    )


def first_judgment(step: JsonObject, baseline: JsonObject, start: str) -> JsonObject:
    """Actual-world time-optimal first MOVE, distinct from eventual arrival.

    False can reflect unavailable change information; it is not a reasoning verdict.
    """
    request = step.get("request")
    appropriate: bool | None = None
    destination = None
    if isinstance(request, dict) and step["engine_submitted"] is True:
        if request["action_type"] == "WAIT":
            appropriate = False
        else:
            route = obj(request["payload"])["route_id"]
            appropriate = route in cast(list[JsonValue], baseline["optimal_first_routes"])
            if type(route) is str and route.startswith(start + "-to-"):
                destination = route.removeprefix(start + "-to-")
    direction = {
        ("A", "D"): "detour-side-move",
        ("A", "C"): "C-side-move",
        ("B", "D"): "D-side-move",
        ("B", "C"): "C-side-move",
    }.get((start, destination or ""), "other-or-no-move")
    return {
        "metric_name": "first-move-appropriateness-after-change-information",
        "appropriate": appropriate,
        "response_status": step["response_status"],
        "execution": step["engine_status"],
        "destination": destination,
        "direction_category": direction,
        "baseline": baseline,
    }


def metrics(
    steps: list[JsonObject], tick: int, stop: str, first: JsonObject | None, baseline: JsonObject
) -> JsonObject:
    known = stop in (
        "ARRIVED",
        "DECISION_LIMIT",
        "TIME_LIMIT",
        "TIME_BUDGET_NOT_SUBMITTED",
        "RESPONSE_FAILURE",
    )
    arrival: bool | None = stop == "ARRIVED" if known else None
    appropriate = first["appropriate"] if first else None
    elapsed = tick - 3
    return {
        "first_appropriate": appropriate,
        "first_response_status": first["response_status"] if first else "ABSENT",
        "eventual_arrival": arrival,
        "recovered_arrival": appropriate is False and arrival is True
        if appropriate is not None and arrival is not None
        else None,
        "decisions": len(steps),
        "submitted_actions": sum(s["engine_submitted"] is True for s in steps),
        "successful_moves": sum(
            isinstance(s["request"], dict)
            and obj(s["request"])["action_type"] == "MOVE"
            and s["engine_status"] == "SUCCEEDED"
            for s in steps
        ),
        "wait_actions": sum(
            isinstance(s["request"], dict)
            and obj(s["request"])["action_type"] == "WAIT"
            and s["engine_submitted"] is True
            for s in steps
        ),
        "elapsed_ticks": elapsed,
        "completion_ticks": elapsed if arrival is True else None,
        "extra_ticks": elapsed - cast(int, baseline["minimum_cost"]) if arrival is True else None,
        "initial_move_ticks": 2,
        "external_advance_ticks": 1,
        "stop": stop,
    }
