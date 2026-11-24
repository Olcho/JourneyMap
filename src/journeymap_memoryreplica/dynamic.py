"""MemoryReplica engine reconstruction from saved frames, without a Provider.

This is the research-side stepping boundary. It owns no HTTP, credentials,
approvals or money. A paid coordinator must commit its existing journal and
settlement before appending a frame to this input. Replaying a prefix is not
permission to transmit, retry or resume a paid run.
"""

import argparse
import base64
import json
from pathlib import Path
from typing import Any, cast

from journeymap.adapters.llm import parse_candidate, strict_json
from journeymap.adapters.route_update import intent
from journeymap.experiments.alderwick import json_value
from journeymap.experiments.pickup_cue import engine_report, step_record, submit_step
from journeymap_memoryreplica.paired import (
    digest,
    encode,
    observe,
    prepare,
    reconstruction_record,
    request,
)
from journeymap_memoryreplica.probe import source_fingerprint, validate_imports
from journeymap_memoryreplica.responses import (
    MAX_RESPONSE_BYTES,
    Frame,
    build_wire,
    decode_frame,
)

VERSION = "memoryreplica-dynamic-saved-frames-1"


def frame_data(frame: Frame) -> dict[str, Any]:
    if type(frame) is not Frame or len(frame.body) > MAX_RESPONSE_BYTES:
        raise ValueError("bounded inert Frame required")
    return {
        "body_base64": base64.b64encode(frame.body).decode("ascii"),
        "http_status": frame.http_status,
        "complete": frame.complete,
        "elapsed_ms": frame.elapsed_ms,
    }


def parse_frame(value: Any) -> Frame:
    if not isinstance(value, dict) or set(value) != {
        "body_base64",
        "http_status",
        "complete",
        "elapsed_ms",
    }:
        raise ValueError("invalid frame record")
    text = value["body_base64"]
    if type(text) is not str or len(text) > 4 * ((MAX_RESPONSE_BYTES + 2) // 3):
        raise ValueError("invalid bounded frame encoding")
    try:
        body = base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid frame encoding") from exc
    frame = Frame(body, value["http_status"], value["complete"], value["elapsed_ms"])
    if frame_data(frame) != value:
        raise ValueError("noncanonical frame encoding")
    return frame


def reconstruct_prefix(
    policy: str,
    frames: tuple[Frame, ...],
    *,
    case_id: str = "case-1",
    max_decisions: int = 6,
    max_ticks: int = 24,
) -> dict[str, Any]:
    """Rebuild the engine and next request using only already recorded responses.

    NEED_RESPONSE is an unfinished research prefix, never a paid-resume status.
    No callback, transport, account state or provider handle is accepted.
    """
    if type(policy) is not str or policy not in ("none", "recent", "full"):
        raise ValueError("invalid memory policy")
    if type(case_id) is not str or not case_id or len(case_id) > 80:
        raise ValueError("invalid case identity")
    if type(max_decisions) is not int or not 1 <= max_decisions <= 6:
        raise ValueError("invalid decision limit")
    if type(max_ticks) is not int or not 1 <= max_ticks <= 24:
        raise ValueError("invalid tick limit")
    if type(frames) is not tuple or len(frames) > 1 + max_decisions:
        raise ValueError("bounded immutable frame tuple required")
    saved_frames = [frame_data(frame) for frame in frames]
    session = prepare("unchanged", "memoryreplica-dynamic-" + case_id + "-" + policy)
    packets: list[dict[str, Any]] = []
    seen: set[str] = set()
    result: dict[str, Any] = {
        "protocol": VERSION,
        "evidence_scope": "SAVED_FRAME_ENGINE_RECONSTRUCTION_NOT_PAID_AUTHORITY",
        "case_id": case_id,
        "memory_policy": policy,
        "max_decisions": max_decisions,
        "max_ticks": max_ticks,
        "frames": saved_frames,
        "packets": packets,
        "reconstruction": {"status": "NOT_RUN"},
        "action_steps": [],
        "stop": "NEED_RESPONSE",
        "next_wire": None,
        "provider_recalled": False,
    }

    def next_packet(text: str) -> dict[str, Any] | None:
        wire = build_wire(text)
        if len(packets) == len(frames):
            result["next_wire"] = wire
            return None
        frame = frames[len(packets)]
        outcome = decode_frame(frame)
        rid = outcome["response_id"]
        if isinstance(rid, str) and rid:
            if rid in seen:
                outcome = {**outcome, "status": "DUPLICATE_RESPONSE_ID", "text": None}
            seen.add(rid)
        packet = {"wire": wire, "outcome": outcome}
        packets.append(packet)
        return outcome

    try:
        observation, semantic, context = observe(session, policy)
        initial = cast(dict[str, Any], session.kernel.state_snapshot)
        start = session.kernel.simulation_time
        deadline = start + max_ticks
        reconstruction_request = request(context, "reconstruction", max_decisions, deadline, "B")
        first_action_request = request(context, "action", max_decisions, deadline, "B")
        result["baseline"] = {
            "tick": start,
            "position": session.position,
            "context_digest": digest(context),
            "full_experience_digest": digest(semantic["event_memory"]),
            "state_digest": session.kernel.state_digest,
            "first_action_request_digest": digest(first_action_request),
        }
        outcome = next_packet(reconstruction_request)
        if outcome is not None:
            if outcome["status"] == "TEXT_READY":
                result["reconstruction"] = reconstruction_record(
                    outcome["text"], context, initial["movement"]["routes"]
                )
            else:
                result["reconstruction"] = {"status": outcome["status"], "recall": None}
            if session.kernel.state_digest != result["baseline"]["state_digest"]:
                raise RuntimeError("reconstruction changed engine state")
            if outcome["status"] not in ("TEXT_READY", "INCOMPLETE", "REFUSAL"):
                result["stop"] = "RECONSTRUCTION_OPERATIONAL_STOP"
            else:
                result["stop"] = "DECISION_LIMIT"
                for index in range(max_decisions):
                    if index:
                        observation, _, context = observe(session, policy)
                    action_request = (
                        first_action_request
                        if index == 0
                        else request(context, "action", max_decisions - index, deadline, "B")
                    )
                    outcome = next_packet(action_request)
                    if outcome is None:
                        result["stop"] = "NEED_RESPONSE"
                        break
                    if outcome["status"] != "TEXT_READY":
                        result["stop"] = "ACTION_" + outcome["status"]
                        break
                    step: dict[str, Any] = {"parser_status": "NOT_RUN", "engine": None}
                    result["action_steps"].append(step)
                    try:
                        text = outcome["text"]
                        if len(text.encode("utf-8")) > 4096:
                            raise ValueError("action response too large")
                        candidate = cast(dict[str, Any], intent(strict_json(text)))
                        action = parse_candidate(candidate, observation)
                    except (ValueError, TypeError, UnicodeError, RecursionError):
                        step["parser_status"] = "INVALID_OUTPUT"
                        result["stop"] = "INVALID_ACTION_OUTPUT"
                        break
                    step["parser_status"] = "VALID"
                    duration = candidate["payload"].get("duration", 0)
                    if candidate["action_type"] == "MOVE":
                        # Stable map only. World data stays on the research side.
                        route = initial["movement"]["routes"].get(candidate["payload"]["route_id"])
                        duration = route["traversal_cost"] if route else 0
                    if session.kernel.simulation_time + duration > deadline:
                        result["stop"] = "TIME_BUDGET_NOT_SUBMITTED"
                        break
                    engine_step = step_record("memoryreplica-dynamic")
                    submit_step(
                        engine_step,
                        observation,
                        action,
                        session.game,
                        session.archive,
                        session.research,
                    )
                    step["engine"] = engine_step
                    if engine_step["failure"] is not None:
                        result["stop"] = "ENGINE_OR_RECORDING_ERROR"
                        break
                    if session.position == "B":
                        result["stop"] = "ARRIVED"
                        break
                    if session.kernel.simulation_time >= deadline:
                        result["stop"] = "TIME_LIMIT"
                        break
        if len(packets) != len(frames):
            raise ValueError("responses remain after the case stopped")
        result["final"] = {
            "position": session.position,
            "tick": session.kernel.simulation_time,
            "elapsed_ticks": session.kernel.simulation_time - start,
            "engine_report": json_value(engine_report(session.research)),
        }
        return result
    finally:
        session.close()


def audit_case(report: dict[str, Any]) -> dict[str, Any]:
    """Recompute all inputs, outcomes, action records and engine results from frames."""
    try:
        if report["protocol"] != VERSION or not isinstance(report["frames"], list):
            raise ValueError("unsupported saved case")
        frames = tuple(parse_frame(value) for value in report["frames"])
        rebuilt = reconstruct_prefix(
            report["memory_policy"],
            frames,
            case_id=report["case_id"],
            max_decisions=report["max_decisions"],
            max_ticks=report["max_ticks"],
        )
        if encode(rebuilt) != encode(report):
            raise ValueError("saved case differs from deterministic reconstruction")
    except (KeyError, TypeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid saved case") from exc
    return {
        "data_only_equal": True,
        "provider_recalled": False,
        "stop": rebuilt["stop"],
        "paid_journal_verified": False,
        "provider_authenticity_verified": False,
    }


def advance_case(value: Any) -> dict[str, Any]:
    """JSON boundary for an isolated research process; no money/approval authority."""
    if not isinstance(value, dict) or set(value) != {
        "memory_policy",
        "frames",
        "case_id",
        "max_decisions",
        "max_ticks",
    }:
        raise ValueError("invalid research prefix request")
    if not isinstance(value["frames"], list) or len(value["frames"]) > 7:
        raise ValueError("invalid bounded prefix")
    return reconstruct_prefix(
        value["memory_policy"],
        tuple(parse_frame(f) for f in value["frames"]),
        case_id=value["case_id"],
        max_decisions=value["max_decisions"],
        max_ticks=value["max_ticks"],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("audit", "prefix"), default="audit")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    validate_imports(args.source_root)
    if not Path(__file__).resolve().is_relative_to((args.source_root / "src").resolve()):
        raise ValueError("dynamic module import/source mismatch")
    current = source_fingerprint(args.source_root)
    document = strict_json(args.input.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or set(document) != {"source_hashes", "cases"}:
        raise ValueError("expected source-bound case bundle")
    if encode(document["source_hashes"]) != encode(current):
        raise ValueError("source inventory mismatch")
    cases = document["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
        raise ValueError("bounded case bundle required")
    result = {"mode": args.mode, "provider_recalled": False, "source_unchanged": True}
    if args.mode == "audit":
        result["checks"] = [audit_case(cast(dict[str, Any], case)) for case in cases]
    else:
        result["cases"] = [advance_case(case) for case in cases]
    if current != source_fingerprint(args.source_root):
        raise ValueError("source changed during reconstruction")
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(encode({"cases": len(cases), "mode": args.mode, "provider_recalled": False}))


if __name__ == "__main__":
    main()
