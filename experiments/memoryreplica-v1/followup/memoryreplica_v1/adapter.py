"""Existing-engine stepping and Responses packets for the frozen v1 protocol.

Only next_wire.body is a provider input. World truth and scores remain in the
research result. HTTP, credentials, and financial journals stay with Actual.
"""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from . import VERSION
from .evaluate import bounded_path, encode, evaluate_reconstruction

POLICIES = tuple("NSFMR")
KNOWN_MODEL_FAILURES = {"INCOMPLETE", "REFUSAL"}


def digest(value):
    return sha256(encode(value).encode()).hexdigest()


def load_protocol(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "freeze_manifest.json").read_text())
    for name, item in manifest["files"].items():
        data = (directory / name).read_bytes()
        if len(data) != item["bytes"] or sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(f"frozen input changed: {name}")
    protocol = json.loads((directory / "protocol.json").read_text())
    if (protocol["primary_contrast"] != "M_minus_R" or protocol["main_worlds"] != 60
            or protocol["archive_routes_per_memory_condition"] != 4
            or protocol["max_followup_decisions"] != 6 or protocol["max_followup_ticks"] != 24):
        raise ValueError("adapter only implements the frozen v1 design")
    prompts = re.findall(r"```text\n(.*?)\n```", (directory / "prompts.md").read_text(), re.S)
    if len(prompts) != 3:
        raise ValueError("expected frozen common, reconstruction, action prompts")
    return protocol, dict(zip(("common", "reconstruction", "action"), prompts, strict=True))


def load_cases(directory, phase):
    if phase not in ("development", "main"):
        raise ValueError("invalid phase")
    filename = "design_validation.json" if phase == "development" else "main_inputs.json"
    dataset = json.loads((Path(directory) / filename).read_text())
    if dataset["phase"] != phase or len(dataset["cases"]) != (6 if phase == "development" else 60):
        raise ValueError("wrong frozen split")
    return dataset["cases"]


def project_observation(observation):
    from journeymap.adapters.llm import observation_json

    data = observation_json(observation)
    sections = {(s["module_id"], s["contributor_id"]): s["content"]
                for s in data["content"]["sections"]}
    position = sections[("movement", "position")]["position"]["location_id"]
    routes = [{k: r[k] for k in ("route_id", "destination", "cost", "passable")}
              for r in sections[("route-update", "local")]["local_routes"]]
    return {"time": data["simulation_time"], "position": position, "local_routes": routes}


def make_wire(context, kind, protocol, prompts):
    from journeymap.adapters.route_update import SCHEMA
    from journeymap_memoryreplica.responses import reconstruction_schema

    if kind not in ("action", "reconstruction"):
        raise ValueError("invalid request kind")
    if set(context) != {"current", "archive", "current_episode_history", "last_receipt", "task"}:
        raise ValueError("provider context has unknown fields")
    request = {"profile": VERSION, "kind": kind,
               "instructions": prompts["common"] + "\n\n" + prompts[kind],
               "context": deepcopy(context)}
    body = {"model": protocol["model_reference"], "input": encode(request),
            "reasoning": {"effort": protocol["reasoning"]},
            "max_output_tokens": protocol["max_output_tokens_per_request"],
            "store": False, "stream": False, "service_tier": protocol["service"],
            "text": {"format": {"type": "json_schema", "name": "memoryreplica_v1_" + kind,
                                "strict": True,
                                "schema": deepcopy(SCHEMA) if kind == "action" else reconstruction_schema()}}}
    serialized = encode(body)
    return {"kind": kind, "body": body, "canonical_utf8": serialized,
            "request_bytes": len(serialized.encode()),
            "sha256": sha256(serialized.encode()).hexdigest(), "wire_version": VERSION}


class Session:
    """Use the preserved engine, replaying the same source experience each time."""

    def __init__(self, case):
        from journeymap import __version__
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
            locations=tuple(Location(n) for n in case["nodes"]),
            routes=tuple(Route(r["route_id"], r["origin"], r["destination"], r["cost"])
                         for r in case["world_truth"]),
            positions=(ActorPosition(actor, case["nodes"][0]),))}
        movement, actions = MovementModule(), ActionRegistry()
        movement.register_actions(actions)
        actions.register("WAIT", 1, WaitHandler())
        self.kernel = create_kernel(modules=(movement,), initial_state=state,
            manifest=RunManifest(case["case_id"], "memory-outcomes", "design-v1", __version__,
                                 1, case["seed"], 0, state_digest(state)),
            action_registry=actions, system_event_registry=SystemEventRegistry())
        self.kernel.boot()
        pipeline = ObservationPipeline()
        for priority, module, name, contributor in (
                (0, "core", "self", contribute_self),
                (10, "movement", "position", contribute_position),
                (16, "route-update", "local", contribute_local)):
            pipeline.register(priority=priority, module_id=module, contributor_id=name,
                              contributor=contributor)
        app, _ = create_application(self.kernel, pipeline=pipeline, perception_extension=perceive_local)
        self.game = app.game_for(actor)
        try:
            for episode in case["source_episodes"]:
                for rid in episode["routes"]:
                    observation = self.game.observe()
                    receipt = self.submit({"action_type": "MOVE", "payload": {"route_id": rid}}, observation)
                    if receipt["status"] != "SUCCEEDED":
                        raise RuntimeError("source history no longer executes")
        except Exception:
            self.close()
            raise

    def observe(self):
        observation = self.game.observe()
        return observation, project_observation(observation)

    def submit(self, candidate, observation):
        from journeymap.adapters.llm import parse_candidate

        receipt = self.game.submit(parse_candidate(candidate, observation))
        return {"status": receipt.status.value, "reason_code": receipt.reason_code,
                "started_at": receipt.started_at, "resolved_at": receipt.resolved_at}

    def close(self):
        self.kernel.close()


def advance(case, policy, saved, protocol, prompts):
    """Consume saved wire-bound frames and return a next request or final result.

    Existing Actual code can persist its response then pass {wire_sha256, frame}.
    No send/retry function is called by this adapter, including on NEED_RESPONSE.
    """
    from journeymap.adapters.llm import strict_json
    from journeymap.adapters.route_update import intent
    from journeymap_memoryreplica.dynamic import parse_frame
    from journeymap_memoryreplica.responses import decode_frame

    if policy not in POLICIES or type(saved) is not list or len(saved) > 7:
        raise ValueError("invalid policy or bounded saved response list")
    session = Session(case)
    result = {"protocol": VERSION, "case_id": case["case_id"],
              "episode_id": case["case_id"] + "--" + policy, "family": case["family"],
              "policy": policy, "input_case_sha256": digest(case), "saved": deepcopy(saved),
              "packets": [], "action_steps": [], "reconstruction": None,
              "stop": "NEED_RESPONSE", "next_wire": None,
              "provider_recalled": False, "score": None, "success": None,
              "usage_price_basis": "legacy_response_decoder_rates_not_current_quote"}
    seen = set()
    steps, packets = result["action_steps"], result["packets"]
    consumed = 0

    def consume(wire):
        nonlocal consumed
        if consumed == len(saved):
            result["next_wire"] = wire
            result["stop"] = "NEED_RESPONSE"
            return None
        item = saved[consumed]
        if set(item) != {"wire_sha256", "frame"} or item["wire_sha256"] != wire["sha256"]:
            raise ValueError("saved response belongs to a different request")
        outcome = decode_frame(parse_frame(item["frame"]))
        response_id = outcome["response_id"]
        if response_id:
            if response_id in seen:
                raise ValueError("duplicate response id within episode")
            seen.add(response_id)
        packets.append({"wire": wire, "outcome": outcome})
        consumed += 1
        return outcome

    try:
        observation, current = session.observe()
        initial = deepcopy(case["contexts"][policy])
        if current != initial["current"]:
            raise RuntimeError("engine end state differs from frozen first context")
        result["baseline"] = {"initial_context_sha256": digest(initial),
                              "state_digest": session.kernel.state_digest,
                              "start_tick": current["time"], "start": current["position"]}
        first_action = make_wire(initial, "action", protocol, prompts)
        result["baseline"]["first_action_sha256"] = first_action["sha256"]
        start_tick, deadline = current["time"], initial["task"]["deadline_tick"]
        oracle = bounded_path(case["world_truth"], current["position"], case["goal"], 6, 24)
        if oracle != case["oracle_path"]:
            raise RuntimeError("constrained reference path changed")
        result["oracle_cost"] = oracle["cost"]
        result["input_context"] = initial
        outcome = consume(make_wire(initial, "reconstruction", protocol, prompts))
        if outcome is not None:
            if outcome["status"] in {"TEXT_READY", *KNOWN_MODEL_FAILURES}:
                result["reconstruction"] = evaluate_reconstruction(
                    case, initial, outcome["text"], outcome["status"])
                history = []
                result["stop"] = "DECISION_LIMIT"
                for index in range(6):
                    if index:
                        observation, current = session.observe()
                    context = {"current": current, "archive": deepcopy(initial["archive"]),
                               "current_episode_history": deepcopy(history),
                               "last_receipt": deepcopy(history[-1]["receipt"]) if history else None,
                               "task": {"goal": case["goal"], "remaining_decisions": 6 - index,
                                        "deadline_tick": deadline}}
                    wire = first_action if index == 0 else make_wire(context, "action", protocol, prompts)
                    outcome = consume(wire)
                    if outcome is None:
                        break
                    if outcome["status"] not in {"TEXT_READY", *KNOWN_MODEL_FAILURES}:
                        result["stop"] = "UNRESOLVED_" + outcome["status"]
                        break
                    step = {"decision": index + 1, "observation": deepcopy(current),
                            "action": None, "receipt": None, "observation_after": None,
                            "status": outcome["status"]}
                    steps.append(step)
                    if outcome["status"] in KNOWN_MODEL_FAILURES:
                        result["stop"] = "MODEL_" + outcome["status"]
                        break
                    try:
                        if len(outcome["text"].encode()) > 4096:
                            raise ValueError("action output too large")
                        candidate = intent(strict_json(outcome["text"]))
                    except (ValueError, TypeError, UnicodeError, RecursionError):
                        step["status"] = "INVALID_ACTION_OUTPUT"
                        result["stop"] = "INVALID_ACTION_OUTPUT"
                        break
                    step["action"] = candidate
                    # Only a currently observed legal edge supplies a time estimate.
                    # Invalid route attempts go through the existing engine rejection.
                    visible = {r["route_id"]: r for r in current["local_routes"]}
                    duration = (candidate["payload"]["duration"] if candidate["action_type"] == "WAIT"
                                else visible.get(candidate["payload"]["route_id"], {}).get("cost", 0))
                    if current["time"] + duration > deadline:
                        step["status"] = "TIME_BUDGET_NOT_SUBMITTED"
                        result["stop"] = "TIME_BUDGET_NOT_SUBMITTED"
                        break
                    receipt = session.submit(candidate, observation)
                    _, after = session.observe()
                    step.update(receipt=receipt, observation_after=after, status=receipt["status"])
                    history.append({k: deepcopy(step[k]) for k in
                                    ("observation", "action", "receipt", "observation_after")})
                    if after["position"] == case["goal"]:
                        result["stop"] = "ARRIVED"
                        break
                    if after["time"] >= deadline:
                        result["stop"] = "TIME_LIMIT"
                        break
            else:
                result["stop"] = "UNRESOLVED_" + outcome["status"]
        if consumed != len(saved):
            raise ValueError("saved responses remain after termination")
        _, final = session.observe()
        result["final"] = {"position": final["position"], "tick": final["time"],
                           "elapsed_ticks": final["time"] - start_tick,
                           "decisions": len(steps), "state_digest": session.kernel.state_digest}
        if result["stop"] != "NEED_RESPONSE" and not result["stop"].startswith("UNRESOLVED_"):
            result["success"] = result["stop"] == "ARRIVED"
            cost = result["final"]["elapsed_ticks"]
            result["score"] = oracle["cost"] / cost if result["success"] else 0.0
            if not 0 <= result["score"] <= 1:
                raise RuntimeError("invalid success-weighted feasible cost score")
        return result
    finally:
        session.close()
