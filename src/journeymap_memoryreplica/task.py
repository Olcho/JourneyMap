"""Task-relevance diagnostics; plans are research outputs, never provider hints."""

import argparse
import heapq
import json
from pathlib import Path
from typing import Any, cast

from journeymap_memoryreplica.paired import (
    OfflineReplies,
    digest,
    encode,
    move,
    observe,
    prepare,
    run_pair,
)
from journeymap_memoryreplica.probe import source_fingerprint
from journeymap_memoryreplica.projection import latest_facts


def shortest_path(edges: list[dict[str, Any]], start: str, goal: str) -> dict[str, Any]:
    frontier = [(0, start, ())]
    best = {start: 0}
    while frontier:
        cost, node, path = heapq.heappop(frontier)
        if cost != best[node]:
            continue
        if node == goal:
            return {"cost": cost, "routes": list(path)}
        for edge in sorted(edges, key=lambda row: row["route_id"]):
            if edge["origin"] != node or edge["passable"] is not True:
                continue
            candidate = cost + edge["cost"]
            destination = edge["destination"]
            if candidate < best.get(destination, float("inf")):
                best[destination] = candidate
                heapq.heappush(frontier, (candidate, destination, (*path, edge["route_id"])))
    return {"cost": None, "routes": None}


def supported_edges(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Require all four explicitly supported attributes; no reverse-edge inference."""
    grouped: dict[str, dict[str, Any]] = {}
    for fact in latest_facts(context):
        grouped.setdefault(fact["route_id"], {})[fact["field"]] = fact["value"]
    return [
        {"route_id": route_id, **fields}
        for route_id, fields in sorted(grouped.items())
        if set(fields) == {"origin", "destination", "cost", "passable"}
    ]


def inspect_inputs() -> dict[str, Any]:
    # Only the stable environment is promoted as this first task-relevance case.
    rows = []
    for policy in ("none", "recent", "full"):
        session = prepare("unchanged", "memoryreplica-task-inspect-" + policy)
        try:
            _, semantic, context = observe(session, policy)
            state = cast(dict[str, Any], session.kernel.state_snapshot["movement"])
            world_edges = [
                {
                    "route_id": rid,
                    "origin": r["origin"],
                    "destination": r["destination"],
                    "cost": r["traversal_cost"],
                    "passable": r["passable"],
                }
                for rid, r in state["routes"].items()
            ]
            edges = supported_edges(context)
            visible_plan = shortest_path(edges, "A", "B")
            rows.append(
                {
                    "memory_policy": policy,
                    "context": context,
                    "context_digest": digest(context),
                    "movement_digest": digest(state),
                    "full_experience_digest": digest(semantic["event_memory"]),
                    "supported_edges": edges,
                    "initial_evidence_plan_to_B": {
                        **visible_plan,
                        "status": "SUPPORTED_PATH"
                        if visible_plan["cost"] is not None
                        else "NO_COMPLETE_PATH_IN_SUPPLIED_EVIDENCE",
                    },
                    "research_only_world_optimum_to_B": shortest_path(world_edges, "A", "B"),
                    "control_evidence_plan_to_G": shortest_path(edges, "A", "G"),
                }
            )
        finally:
            session.close()
    return {"case": "stable-A13-goal-B", "goal": "B", "condition": "unchanged", "rows": rows}


def run_witnesses(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for row in inputs["rows"]:
        policy = row["memory_policy"]
        text = encode({"facts": latest_facts(row["context"])})
        # Both paths are tested in every condition. They are not policy decisions.
        for label, path in (
            ("short_path", ("A-to-S", "S-to-B")),
            ("long_path", ("A-to-D", "D-to-B")),
        ):
            pair = run_pair(
                "unchanged",
                policy,
                OfflineReplies((text,)),
                OfflineReplies(tuple(move(route) for route in path)),
                goal="B",
            )
            pair["scripted_path_label"] = label
            results.append(pair)
    return results


def collect() -> dict[str, Any]:
    inputs = inspect_inputs()
    return {
        "schema": "memoryreplica-task-relevance-offline-1",
        "evidence_kind": "INPUT_GRAPH_AND_SCRIPTED_ENGINE_ONLY",
        "external_api_calls": 0,
        "model_memory_effect_measured": False,
        "inputs": inputs,
        "witnesses": run_witnesses(inputs),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root", "--reference-root", dest="source_root", type=Path, required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import journeymap
    from journeymap_memoryreplica.probe import validate_imports

    validate_imports(args.source_root)
    if not Path(journeymap.__file__).resolve().is_relative_to((args.source_root / "src").resolve()):
        raise ValueError("current source/import mismatch")
    before = source_fingerprint(args.source_root)
    result = collect()
    if before != source_fingerprint(args.source_root):
        raise ValueError("source changed")
    result["current_source_hashes"] = before
    result["current_source_root"] = str(args.source_root.resolve())
    result["source_unchanged"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        encode(
            {
                "memory_conditions": 3,
                "scripted_paths": 6,
                "external_api_calls": 0,
                "source_files_unchanged": len(before),
            }
        )
    )


if __name__ == "__main__":
    main()
