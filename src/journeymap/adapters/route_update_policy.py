"""First-move-only code baselines; input is semantic agent evidence, never truth."""

import heapq
from typing import cast

from journeymap.adapters.llm import Record
from journeymap.adapters.route_update import map_routes, obj, rows, shape
from journeymap.core.canonical import JsonObject, JsonValue


def shortest_moves(routes: list[JsonObject], origin: str, goal: str = "G") -> JsonObject:
    """Reverse Dijkstra, retaining every optimal directed first edge."""
    graph = map_routes(cast(list[JsonValue], routes))
    distances = {goal: 0}
    heap: list[tuple[int, str]] = [(0, goal)]
    while heap:
        cost, destination = heapq.heappop(heap)
        if cost != distances[destination]:
            continue
        for route in graph:
            if route["destination"] != destination or route["passable"] is not True:
                continue
            previous = cast(str, route["origin"])
            candidate = cost + cast(int, route["cost"])
            if previous not in distances or candidate < distances[previous]:
                distances[previous] = candidate
                heapq.heappush(heap, (candidate, previous))
    best = distances.get(origin)
    optimal: list[str] = sorted(
        cast(str, route["route_id"])
        for route in graph
        if route["origin"] == origin
        and route["passable"] is True
        and cast(str, route["destination"]) in distances
        and cast(int, route["cost"]) + distances[cast(str, route["destination"])] == best
    )
    return {"minimum_cost": best, "optimal_first_routes": cast(list[JsonValue], optimal)}


def first_policy(inputs: JsonObject, policy: str) -> JsonObject:
    if policy not in ("OLD-MAP", "RECEIVED-UPDATE"):
        raise ValueError("unknown first-move policy")
    shape(inputs, "observation event_memory")
    observation = obj(inputs["observation"])
    if (
        observation["time"] != 3
        or observation["position"] not in ("A", "B")
        or len(rows(inputs["event_memory"])) != 1
    ):
        raise ValueError("baselines only apply to first evaluation move")
    knowledge = rows(observation["knowledge"])
    initial = [
        obj(r["claim"])["routes"]
        for r in knowledge
        if obj(r["claim"])["kind"] == "initial-map" and r["source_kind"] == "AUTHORED_INITIAL"
    ]
    if len(initial) != 1:
        raise ValueError("single authored map required")
    graph = rows(Record.capture({"routes": initial[0]}).data["routes"])
    if policy == "RECEIVED-UPDATE":
        for update in knowledge:
            if update["source_kind"] != "OFFICIAL_NOTICE":
                continue
            claim = obj(update["claim"])
            if claim["kind"] != "route-status":
                raise ValueError("unsupported official claim")
            for edge in graph:
                if edge["route_id"] in cast(list[JsonValue], claim["route_ids"]):
                    edge["passable"] = claim["passable"]
    choice = shortest_moves(graph, observation["position"])
    optimal = cast(list[JsonValue], choice["optimal_first_routes"])
    return {
        "policy": policy,
        "input": Record.capture(inputs).data,
        **choice,
        "selected_route": optimal[0] if optimal else None,
        "tie_break": "lexicographically smallest route_id; full optimal set retained",
        "scope": "first-evaluation-move-only",
    }
