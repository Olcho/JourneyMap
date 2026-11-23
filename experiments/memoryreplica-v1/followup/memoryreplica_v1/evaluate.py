"""Semantic reconstruction, evidence support, and constrained connectivity."""

import heapq
import json
import math

FIELDS = ("origin", "destination", "cost", "passable")
EDGE_FIELDS = ("route_id", *FIELDS)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def same(a, b):
    return type(a) is type(b) and a == b


def edge_key(edge):
    # JSON distinguishes true from 1, unlike tuple equality in Python.
    return encode([edge.get(field) for field in EDGE_FIELDS])


def bounded_path(edges, start, goal, decisions=6, ticks=24):
    queue, best = [(0, 0, start, ())], {(start, 0): 0}
    while queue:
        cost, count, node, path = heapq.heappop(queue)
        if cost != best[(node, count)]:
            continue
        if node == goal:
            return {"cost": cost, "routes": list(path)}
        if count == decisions:
            continue
        for edge in edges:
            if edge["origin"] != node or not edge["passable"]:
                continue
            other, new_cost = edge["destination"], cost + edge["cost"]
            key = (other, count + 1)
            if new_cost <= ticks and new_cost < best.get(key, math.inf):
                best[key] = new_cost
                heapq.heappush(queue, (new_cost, count + 1, other,
                                       path + (edge["route_id"],)))
    return {"cost": None, "routes": None}


def input_evidence(context):
    expected = {}
    for i, record in enumerate(context["archive"]):
        for field in FIELDS:
            expected[(record["route_id"], field)] = {
                "value": record[field], "as_of": record["observed_at"],
                "evidence": f"archive/{i}"}
    current = context["current"]
    for i, record in enumerate(current["local_routes"]):
        for field in FIELDS:
            value = current["position"] if field == "origin" else record[field]
            expected[(record["route_id"], field)] = {
                "value": value, "as_of": current["time"],
                "evidence": f"current/local_routes/{i}"}
    return expected


def supplied_edges(context):
    result = {}
    for (route_id, field), fact in input_evidence(context).items():
        result.setdefault(route_id, {"route_id": route_id})[field] = fact["value"]
    return list(result.values())


def well_typed(edge):
    return (all(type(edge.get(k)) is str and edge[k] for k in ("route_id", "origin", "destination"))
            and type(edge.get("cost")) is int and edge["cost"] > 0
            and type(edge.get("passable")) is bool)


def parse_facts(text):
    from journeymap.adapters.llm import strict_json

    document = strict_json(text)
    if type(document) is not dict or set(document) != {"facts"}:
        raise ValueError("expected facts object")
    facts = document["facts"]
    if type(facts) is not list or len(facts) > 512:
        raise ValueError("invalid facts list")
    seen = set()
    for fact in facts:
        if type(fact) is not dict or set(fact) != {"route_id", "field", "value", "as_of", "evidence"}:
            raise ValueError("invalid fact fields")
        if type(fact["route_id"]) is not str or not fact["route_id"] or fact["field"] not in FIELDS:
            raise ValueError("invalid route or field")
        key = (fact["route_id"], fact["field"])
        if key in seen:
            raise ValueError("duplicate route field")
        seen.add(key)
        if fact["value"] is None:
            if fact["as_of"] is not None or fact["evidence"] is not None:
                raise ValueError("unknown fact must have null provenance")
        elif type(fact["value"]) not in (str, int, bool):
            raise ValueError("unsupported fact value type")
        if fact["as_of"] is not None and (type(fact["as_of"]) is not int or fact["as_of"] < 0):
            raise ValueError("invalid timestamp")
        if fact["evidence"] is not None and type(fact["evidence"]) is not str:
            raise ValueError("invalid evidence")
    return facts


def evaluate_reconstruction(case, context, text=None, status="TEXT_READY"):
    facts, parse_error = [], None
    if status == "TEXT_READY":
        try:
            facts = parse_facts(text)
        except (ValueError, TypeError, UnicodeError, RecursionError) as error:
            status, parse_error = "INVALID_OUTPUT", str(error)
    expected = input_evidence(context)
    truth = {r["route_id"]: r for r in case["world_truth"]}
    expected_ids = {key[0] for key in expected}
    observed_ids = {r["route_id"] for r in case["source_observation_records"]} | expected_ids
    claimed, by_key, known_count, correct, supported, unprovided_correct = {}, {}, 0, 0, 0, 0
    for fact in facts:
        key = (fact["route_id"], fact["field"])
        by_key[key] = fact
        if fact["value"] is None:
            continue
        known_count += 1
        claimed.setdefault(key[0], {"route_id": key[0]})[key[1]] = fact["value"]
        target = truth.get(key[0], {})
        if key[1] in target and same(fact["value"], target[key[1]]):
            correct += 1
            unprovided_correct += key not in expected
        evidence = expected.get(key)
        if evidence and all(same(fact[k], evidence[k]) for k in ("value", "as_of", "evidence")):
            supported += 1
    exact_ids = {rid for rid, edge in claimed.items()
                 if rid in truth and edge_key(edge) == edge_key(truth[rid])}
    correct_input = sum(key in by_key and same(by_key[key]["value"], target["value"])
                        for key, target in expected.items())
    input_unknown = sum(key in by_key and by_key[key]["value"] is None for key in expected)
    input_missing = sum(key not in by_key for key in expected)
    start, goal = context["current"]["position"], context["task"]["goal"]
    budget = context["task"]["deadline_tick"] - context["current"]["time"]
    limit = context["task"]["remaining_decisions"]
    provided = supplied_edges(context)
    valid_claimed = [r for r in claimed.values() if well_typed(r)]
    retained_regions = sorted({r["origin"] for r in context["archive"]})
    ages = [context["current"]["time"] - r["observed_at"] for r in context["archive"]]
    return {
        "status": status, "parse_error": parse_error,
        "world_edges": len(truth), "observed_edges": len(observed_ids),
        "provided_edges": len(expected_ids), "claimed_edges": len(claimed),
        "exact_edges": len(exact_ids),
        "world_edge_f1": 2 * len(exact_ids) / (len(truth) + len(claimed)),
        "observed_coverage": len(observed_ids) / len(truth),
        "retention_dropped_edges": sorted(observed_ids - expected_ids),
        "retention_loss_count": len(observed_ids - expected_ids),
        "known_claims": known_count, "semantic_correct_facts": correct,
        "supported_claims": supported,
        "provenance_accuracy": supported / known_count if known_count else None,
        "correct_but_unprovided_facts": unprovided_correct,
        "input_expected_facts": len(expected), "input_correct_facts": correct_input,
        "input_conditional_accuracy": correct_input / len(expected),
        "input_missing_facts": input_missing, "input_unknown_facts": input_unknown,
        "input_wrong_facts": len(expected) - correct_input - input_missing - input_unknown,
        "archive_mean_age_ticks": sum(ages) / len(ages) if ages else None,
        "archive_origin_regions": retained_regions,
        "input_path": bounded_path(provided, start, goal, limit, budget),
        "claimed_path": bounded_path(valid_claimed, start, goal, limit, budget),
        "truth_validated_claimed_path": bounded_path(
            [claimed[rid] for rid in sorted(exact_ids)], start, goal, limit, budget),
        "claimed_graph": [claimed[rid] for rid in sorted(claimed)],
        "exact_route_ids": sorted(exact_ids), "facts": facts,
    }
