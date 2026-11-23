"""Research-only arrival evidence and directed fixture baselines, separate from prompts."""

import heapq
from typing import cast

from journeymap.core.canonical import JsonObject
from journeymap.experiments.memory_horizon_audit import obj, require, rows


def minimum_cost(initial: JsonObject, target: str) -> tuple[int, int]:
    routes = rows(list(obj(obj(initial["movement"])["routes"]).values()))
    frontier: list[tuple[int, int, str]] = [(0, 0, "village-square")]
    seen: set[str] = set()
    while frontier:
        ticks, actions, place = heapq.heappop(frontier)
        if place in seen:
            continue
        if place == target:
            # This fixture's unique one-edge optimum also minimizes action count.
            return actions, ticks
        seen.add(place)
        for route in routes:
            if route["origin"] == place and route.get("passable") is True:
                heapq.heappush(
                    frontier,
                    (
                        ticks + cast(int, route["traversal_cost"]),
                        actions + 1,
                        cast(str, route["destination"]),
                    ),
                )
    raise ValueError("no legal fixture path")


def arrival(step: JsonObject, report: JsonObject, target: str) -> bool:
    request, receipt = step["request"], step["receipt"]
    if not isinstance(request, dict) or not isinstance(receipt, dict):
        return False
    matches = [
        r
        for r in rows(report["action_results"])
        if r["action_request_id"] == request["action_request_id"]
    ]
    positions = obj(obj(report["final_state"])["movement"])["positions"]
    return (
        step["engine_submitted"] is True
        and request["action_type"] == "MOVE"
        and receipt["status"] == "SUCCEEDED"
        and len(matches) == 1
        and matches[0]["status"] == "SUCCEEDED"
        and obj(obj(positions)["stranger"])["location_id"] == target
        and any(
            e["event_type"] == "ActorMoved"
            and e["source_ref"] == request["action_request_id"]
            and obj(e["payload"])["destination"] == target
            for e in rows(report["events"])
        )
    )


def metrics(data: JsonObject, stop: str) -> JsonObject:
    steps = rows(data["steps"])
    baseline_actions, baseline_ticks = minimum_cost(
        obj(data["initial_state"]), cast(str, obj(data["case"])["target"])
    )
    submitted = sum(s["engine_submitted"] is True for s in steps)
    success_steps = [s for s in steps if s["arrived"] is True]
    success = bool(success_steps)
    ticks = cast(int, obj(data["engine_report"])["final_simulation_time"]) - 7
    pending = data.get("pending_step")
    observed = [*steps, obj(pending)] if isinstance(pending, dict) else steps
    rechecks = [s["step_number"] for s in observed if obj(s["cue"])["cue_current_present"] is True]
    immediate: bool | None = None
    if steps and steps[0]["category"] in ("CONTINUE", "SUCCESS", "RESPONSE_FAILURE"):
        immediate = steps[0]["arrived"] is True
    known_final = stop in ("SUCCESS", "DECISION_LIMIT", "RESPONSE_FAILURE")
    eventual: bool | None = success if known_final else None
    require(not success or stop == "SUCCESS", "arrival must terminate case")
    return {
        "immediate_success": immediate,
        "eventual_success": eventual,
        "recovered_success": (not immediate and eventual)
        if immediate is not None and eventual is not None
        else None,
        "decision_attempts": len(observed),
        "submitted_actions": submitted,
        "successful_moves": sum(
            isinstance(s["request"], dict)
            and obj(s["request"])["action_type"] == "MOVE"
            and isinstance(s["receipt"], dict)
            and obj(s["receipt"])["status"] == "SUCCEEDED"
            for s in steps
        ),
        "wait_count": sum(
            isinstance(s["request"], dict)
            and obj(s["request"])["action_type"] == "WAIT"
            and s["engine_submitted"] is True
            for s in steps
        ),
        "elapsed_ticks": ticks if data["prefix_status"] == "READY" else None,
        "baseline_actions": baseline_actions,
        "baseline_ticks": baseline_ticks,
        "steps_to_success": len(steps) if success else None,
        "ticks_to_success": ticks if success else None,
        "extra_actions": submitted - baseline_actions if success else None,
        "extra_ticks": ticks - baseline_ticks if success else None,
        "recheck_count": len(rechecks),
        "first_recheck_step": rechecks[0] if rechecks else None,
        "stop_reason": stop,
    }
