"""Explicit synthetic responses used solely for engine/protocol checks."""

from copy import deepcopy
import json

from memoryreplica_v1.adapter import advance
from memoryreplica_v1.evaluate import bounded_path, input_evidence, supplied_edges
from journeymap_memoryreplica.dynamic import frame_data
from journeymap_memoryreplica.responses import Frame, MODEL


def frame(text, response_id, *, status="completed", http=200, refusal=False, complete=True):
    content = {"type": "refusal", "refusal": "fixture"} if refusal else {"type": "output_text", "text": text}
    body = {"id": response_id, "model": MODEL, "service_tier": "default", "status": status,
            "error": None, "incomplete_details": None if status == "completed" else {"reason": "max_output_tokens"},
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                      "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 0}},
            "output": [{"type": "message", "role": "assistant", "status": "completed", "content": [content]}]}
    return frame_data(Frame(json.dumps(body).encode(), http_status=http, complete=complete))


def answer(report, text, **options):
    return {"wire_sha256": report["next_wire"]["sha256"],
            "frame": frame(text, f"fixture-{report['case_id']}-{report['policy']}-{len(report['saved'])}", **options)}


def perfect_reconstruction(context):
    return json.dumps({"facts": [{"route_id": rid, "field": field, **fact}
                                  for (rid, field), fact in input_evidence(context).items()]})


def context_planner(context):
    """Deterministic fixture driver. Uses only supplied observations, never truth."""
    known = {}
    for record in context["current_episode_history"]:
        old_context = {"archive": [], "current": record["observation"]}
        for edge in supplied_edges(old_context):
            known[edge["route_id"]] = edge
    for edge in supplied_edges(context):
        known[edge["route_id"]] = edge
    current, task = context["current"], context["task"]
    path = bounded_path(list(known.values()), current["position"], task["goal"],
                        task["remaining_decisions"], task["deadline_tick"] - current["time"])
    if path["routes"]:
        route_id = path["routes"][0]
    else:
        seen = {x["observation"]["position"] for x in context["current_episode_history"]}
        candidates = [r for r in current["local_routes"]
                      if current["time"] + r["cost"] <= task["deadline_tick"]]
        if not candidates:
            return {"action_type": "WAIT", "payload": {"duration": 1}}
        route_id = min(candidates, key=lambda r: (r["destination"] in seen, r["cost"], r["route_id"]))["route_id"]
    return {"action_type": "MOVE", "payload": {"route_id": route_id}}


def run_fixture(case, policy, protocol, prompts):
    saved = []
    report = advance(case, policy, saved, protocol, prompts)
    while report["next_wire"] is not None:
        request = json.loads(report["next_wire"]["body"]["input"])
        context = request["context"]
        text = (perfect_reconstruction(context) if request["kind"] == "reconstruction"
                else json.dumps(context_planner(context)))
        saved.append(answer(report, text))
        report = advance(case, policy, deepcopy(saved), protocol, prompts)
    return report
