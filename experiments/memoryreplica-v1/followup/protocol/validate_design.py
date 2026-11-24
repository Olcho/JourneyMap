"""Offline design verification. No model calls and no production-source edits.

Uses the existing JourneyMap engine for all historical actions. Memory entries
are spatial facts observed during tasks, tagged by the task's eventual outcome.
Generated reference reconstructions are not predictions from an AI model.
Requires Python 3.12+ and the preserved research/src tree.
"""
import argparse
from copy import deepcopy
import hashlib
import heapq
import itertools
import json
import math
from pathlib import Path
import random
import sys


class DesignIneligible(ValueError):
    """Only preregistered input-eligibility failures can trigger resampling."""


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def source_hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*.py"))}


def graph(family, seed):
    rng = random.Random(seed)
    n = rng.randint(8, 12)
    links = set()
    def link(a, b):
        links.add(tuple(sorted((a, b))))
    if family == "ring":
        for i in range(n):
            link(i, (i + 1) % n)
        candidates = list(itertools.combinations(range(n), 2))
        rng.shuffle(candidates)
        for a, b in candidates[:2]:
            link(a, b)
    elif family == "hub":
        for i in range(1, n):
            link(0, i)
        for i in range(1, n - 1):
            if rng.random() < .6:
                link(i, i + 1)
    elif family == "bridge":
        split = n // 2
        for block in (list(range(split)), list(range(split, n))):
            for i, a in enumerate(block):
                link(a, block[(i + 1) % len(block)])
        link(split - 1, split)
    else:
        raise ValueError(family)
    weighted = [(a, b, rng.randint(1, 4)) for a, b in sorted(links)]
    names = [f"v{i:02d}" for i in range(n)]
    rng.shuffle(names)
    directed = [(names[a], names[b], cost) for a, b, cost in weighted
                for a, b in ((a, b), (b, a))]
    rng.shuffle(directed)
    routes = [{"route_id": f"r{i:03d}", "origin": a, "destination": b,
               "cost": cost, "passable": True}
              for i, (a, b, cost) in enumerate(directed)]
    return names, routes, digest(weighted)


def path_cost(routes, start, goal, use_unit_cost=False):
    queue, best = [(0, start, ())], {start: 0}
    while queue:
        cost, node, path = heapq.heappop(queue)
        if cost != best[node]:
            continue
        if node == goal:
            return {"cost": cost, "routes": list(path)}
        for edge in routes:
            if edge["origin"] != node or not edge["passable"]:
                continue
            new_cost = cost + (1 if use_unit_cost else edge["cost"])
            other = edge["destination"]
            if new_cost < best.get(other, math.inf):
                best[other] = new_cost
                heapq.heappush(queue, (new_cost, other, path + (edge["route_id"],)))
    return {"cost": None, "routes": None}


def paths_three(routes, start):
    def walk(nodes, route_ids):
        if len(route_ids) == 3:
            yield nodes, route_ids
            return
        for edge in routes:
            if edge["origin"] == nodes[-1] and edge["destination"] not in nodes:
                yield from walk(nodes + [edge["destination"]], route_ids + [edge["route_id"]])
    return list(walk([start], []))


def bounded_path(routes, start, goal, decisions=6, ticks=24):
    queue, best = [(0, 0, start, ())], {(start, 0): 0}
    while queue:
        cost, count, node, path = heapq.heappop(queue)
        if cost != best[(node, count)]:
            continue
        if node == goal:
            return {"cost": cost, "routes": list(path)}
        if count == decisions:
            continue
        for edge in routes:
            if edge["origin"] != node or not edge["passable"]:
                continue
            new_cost, other = cost + edge["cost"], edge["destination"]
            key = (other, count + 1)
            if new_cost <= ticks and new_cost < best.get(key, math.inf):
                best[key] = new_cost
                heapq.heappush(queue, (new_cost, count + 1, other, path + (edge["route_id"],)))
    return {"cost": None, "routes": None}


def plan_history(nodes, routes, seed, order_index):
    rng = random.Random(seed + 200000)
    patterns = list(itertools.combinations(range(4), 2))
    success_positions = set(patterns[order_index % len(patterns)])
    current = nodes[0]
    planned = []
    for i in range(4):
        candidates = paths_three(routes, current)
        rng.shuffle(candidates)
        for visited, moves in candidates:
            missing_goals = [g for g in sorted(nodes) if g not in visited
                             and (path_cost(routes, current, g, True)["cost"] or 99) <= 3]
            if i in success_positions or missing_goals:
                goal = visited[-1] if i in success_positions else rng.choice(missing_goals)
                planned.append({"episode_id": f"e{i:02d}", "start": current,
                                "goal": goal, "action_limit": 3, "routes": moves})
                current = visited[-1]
                break
        else:
            raise DesignIneligible("No admissible three-action source task")
    return planned


def run_history(nodes, routes, planned, case_id, seed):
    from journeymap import __version__
    from journeymap.adapters.llm import observation_json, parse_candidate
    from journeymap.application.observations import ObservationPipeline, contribute_self
    from journeymap.bootstrap import create_application, create_kernel
    from journeymap.core.actions import WaitHandler
    from journeymap.core.canonical import state_digest
    from journeymap.core.entities import Entity, entity_state
    from journeymap.core.handlers import ActionRegistry, SystemEventRegistry
    from journeymap.core.run import RunManifest
    from journeymap.modules.movement import ActorPosition, Location, MovementModule, Route, movement_state
    from journeymap.modules.movement.perception import contribute_position
    from journeymap.scenarios.route_update import contribute_local, perceive_local

    actor = "traveller"
    state = {"entities": entity_state((Entity(actor, "actor"),)), "movement": movement_state(
        locations=tuple(Location(n) for n in nodes),
        routes=tuple(Route(r["route_id"], r["origin"], r["destination"], r["cost"]) for r in routes),
        positions=(ActorPosition(actor, nodes[0]),))}
    movement, actions = MovementModule(), ActionRegistry()
    movement.register_actions(actions)
    actions.register("WAIT", 1, WaitHandler())
    kernel = create_kernel(modules=(movement,), initial_state=state,
        manifest=RunManifest(case_id, "memory-outcomes", "design-v1", __version__, 1,
                             seed, 0, state_digest(state)),
        action_registry=actions, system_event_registry=SystemEventRegistry())
    kernel.boot()
    pipeline = ObservationPipeline()
    for priority, module, name, contributor in (
        (0, "core", "self", contribute_self),
        (10, "movement", "position", contribute_position),
        (16, "route-update", "local", contribute_local)):
        pipeline.register(priority=priority, module_id=module, contributor_id=name, contributor=contributor)
    app, _ = create_application(kernel, pipeline=pipeline, perception_extension=perceive_local)
    game = app.game_for(actor)

    def observe():
        observation = game.observe()
        data = observation_json(observation)
        sections = {(s["module_id"], s["contributor_id"]): s["content"]
                    for s in data["content"]["sections"]}
        visible = {"time": data["simulation_time"],
                   "position": sections[("movement", "position")]["position"]["location_id"],
                   "local_routes": sections[("route-update", "local")]["local_routes"]}
        return observation, visible

    episodes, records = [], []
    try:
        for plan in planned:
            facts, receipts, visited = [], [], [plan["start"]]
            for rid in plan["routes"]:
                observation, visible = observe()
                for edge in visible["local_routes"]:
                    facts.append({"observed_at": visible["time"], "origin": visible["position"], **edge})
                action = parse_candidate({"action_type": "MOVE", "payload": {"route_id": rid}}, observation)
                receipt = game.submit(action)
                receipts.append({"status": receipt.status.value if hasattr(receipt.status, "value") else receipt.status,
                                 "reason_code": receipt.reason_code})
                visited.append(kernel.state_snapshot["movement"]["positions"][actor]["location_id"])
            outcome = "SUCCESS" if plan["goal"] in visited[1:] else "FAILURE"
            if plan["goal"] in visited[1:]:
                assert visited[-1] == plan["goal"] and plan["goal"] not in visited[1:-1]
            assert all(r["status"] == "SUCCEEDED" for r in receipts)
            meta = {"episode_id": plan["episode_id"], "episode_goal": plan["goal"],
                    "episode_outcome": outcome}
            records.extend({**meta, **fact} for fact in facts)
            episodes.append({**plan, "outcome": outcome, "visited": visited, "receipts": receipts})
        _, current = observe()
        return episodes, records, current
    finally:
        kernel.close()


def memory_records(records, current, policy):
    current_ids = {r["route_id"] for r in current["local_routes"]}
    def eligible(outcome=None):
        latest = {}
        for record in records:
            if record["route_id"] in current_ids:
                continue
            if outcome is None or record["episode_outcome"] == outcome:
                latest[record["route_id"]] = record
        return sorted(latest.values(), key=lambda r: (r["observed_at"], r["route_id"]), reverse=True)
    s, f = eligible("SUCCESS"), eligible("FAILURE")
    if len(s) < 4 or len(f) < 4:
        raise DesignIneligible("Each outcome pool must contain at least four noncurrent unique routes")
    if policy == "N":
        chosen = []
    elif policy == "S":
        chosen = s[:4]
    elif policy == "F":
        chosen = f[:4]
    elif policy == "R":
        chosen = eligible()[:4]
    elif policy == "M":
        chosen, used = [], set()
        for pool in (s, f, s, f):
            item = next(x for x in pool if x["route_id"] not in used)
            chosen.append(item)
            used.add(item["route_id"])
    else:
        raise ValueError(policy)
    return sorted(chosen, key=lambda r: (r["observed_at"], r["route_id"]))


def visible_edges(context):
    edges = {r["route_id"]: {k: r[k] for k in ("route_id", "origin", "destination", "cost", "passable")}
             for r in context["archive"]}
    for r in context["current"]["local_routes"]:
        edges[r["route_id"]] = {"origin": context["current"]["position"], **r}
    return list(edges.values())


def edge_f1(claims, truth):
    fields = ("route_id", "origin", "destination", "cost", "passable")
    target = {tuple(r[k] for k in fields) for r in truth}
    prediction = {tuple(r[k] for k in fields) for r in claims}
    tp = len(target & prediction)
    return 2 * tp / (len(target) + len(prediction)) if target or prediction else 1.0


def make_case(family, seed, case_id, order_index):
    nodes, routes, world_signature = graph(family, seed)
    planned = plan_history(nodes, routes, seed, order_index)
    episodes, records, current = run_history(nodes, routes, planned, case_id, seed)
    contexts = {p: {"current": deepcopy(current), "archive": memory_records(records, current, p),
                    "current_episode_history": [], "last_receipt": None} for p in "NSFMR"}
    full_ids = {r["origin"] for r in records} | {r["destination"] for r in records}
    goals = [n for n in sorted(full_ids) if n != current["position"]
             and 2 <= (path_cost(routes, current["position"], n, True)["cost"] or 99) <= 4]
    if not goals:
        raise DesignIneligible("No previously encountered nonadjacent follow-up goal")
    goal = random.Random(seed + 400000).choice(goals)
    rows = []
    for policy, context in contexts.items():
        context["task"] = {"goal": goal, "remaining_decisions": 6,
                           "deadline_tick": current["time"] + 24}
        edges = visible_edges(context)
        rows.append({"policy": policy, "archive_records": len(context["archive"]),
                     "unique_archive_routes": len({r["route_id"] for r in context["archive"]}),
                     "archive_bytes": len(encode(context["archive"]).encode()),
                     "input_sha256": digest(context),
                     "provided_edge_count": len(edges),
                     "reference_only_map_f1": edge_f1(edges, routes),
                     "reference_only_known_path": bounded_path(edges, current["position"], goal)})
    assert [e["outcome"] for e in episodes].count("SUCCESS") == 2
    assert [e["outcome"] for e in episodes].count("FAILURE") == 2
    assert all(r["archive_records"] == r["unique_archive_routes"] == 4 for r in rows if r["policy"] != "N")
    assert len({r["provided_edge_count"] for r in rows if r["policy"] != "N"}) == 1
    assert len({digest(c["current"]) for c in contexts.values()}) == 1
    assert len({digest(c["task"]) for c in contexts.values()}) == 1
    assert {r["episode_outcome"] for r in contexts["M"]["archive"]} == {"SUCCESS", "FAILURE"}
    assert edge_f1(routes, routes) == 1 and edge_f1([], routes) == 0
    wrong = deepcopy(routes)
    wrong[0]["cost"] += 1
    assert edge_f1(wrong, routes) < 1
    cited = [{**r, "evidence": "wrong-reference"} for r in routes]
    assert edge_f1(cited, routes) == 1  # Semantic accuracy does not depend on provenance spelling.
    assert bounded_path(routes, current["position"], goal)["cost"] is not None
    return {"case_id": case_id, "family": family, "seed": seed, "order_index": order_index % 6,
            "world_signature": world_signature, "nodes": nodes, "world_truth": routes,
            "source_episodes": episodes, "source_observation_records": records,
            "goal": goal, "start": current["position"], "rows": rows,
            "contexts": contexts, "model_outputs": None,
            "oracle_path": bounded_path(routes, current["position"], goal)}


def collect(phase):
    cases, rejected = [], []
    replicas = 2 if phase == "development" else 20
    for family_index, family in enumerate(("ring", "hub", "bridge")):
        for replica in range(replicas):
            base = (10000 if phase == "development" else 100000) + family_index * 10000 + replica * 100
            for attempt in range(20):
                seed = base + attempt
                try:
                    case = make_case(family, seed, f"{phase}-{family}-{replica}",
                                     family_index * replicas + replica)
                except DesignIneligible as error:
                    rejected.append({"family": family, "seed": seed, "reason": str(error)})
                    continue
                cases.append(case)
                break
            else:
                raise RuntimeError("Input fixture generation exhausted")
    assert len({c["world_signature"] for c in cases}) == 3 * replicas
    return {"schema": "memoryreplica-design-validation-1", "evidence_kind": "SCRIPTED_ENGINE_AND_INPUT_ONLY",
            "phase": phase, "external_api_calls": 0, "llm_effect_measured": False,
            "cases": cases, "rejected_candidates": rejected,
            "checked_properties": ["real_engine_execution", "continuous_history_without_position_reset",
                "two_success_two_failure_source_tasks", "all_action_receipts_distinguished_from_task_outcome",
                "identical_current_state_and_goal_across_conditions", "four_unique_noncurrent_memory_edges",
                "equal_supplied_edge_count_S_F_M_R", "mixed_quota_and_no_duplicate_routes",
                "semantic_score_separate_from_provenance", "reference_outputs_not_llm_predictions",
                "oracle_respects_action_and_tick_limits", "source_outcome_order_counterbalanced"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-src", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("development", "main"), default="development")
    args = parser.parse_args()
    source = args.source_src.resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    before = source_hashes(source)
    result = collect(args.phase)
    assert before == source_hashes(source)
    result["source_files_unchanged"] = len(before)
    result["source_hashes"] = before
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"phase": args.phase, "cases": len(result["cases"]), "contexts": 5 * len(result["cases"]),
                     "scripted_moves": 12 * len(result["cases"]), "external_api_calls": 0,
                     "rejected_candidates": result["rejected_candidates"],
                     "source_files_unchanged": len(before),
                     "rows": [{"case": c["case_id"], "start": c["start"], "goal": c["goal"],
                               "known_path_cost": {r["policy"]: r["reference_only_known_path"]["cost"] for r in c["rows"]},
                               "archive_bytes": {r["policy"]: r["archive_bytes"] for r in c["rows"]}}
                              for c in result["cases"]] if args.phase == "development" else "See output JSON"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
