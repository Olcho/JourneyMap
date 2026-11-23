"""One v3-smoke to v4-pilot transition; saved evidence, no network/Git/path lookup."""

from hashlib import sha256
from typing import cast

from journeymap.adapters.route_update import obj, rows
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.experiments.pilot_journal import digest
from journeymap.experiments.provenance import logical_source_sha256

BASE_SOURCE = "94150b4f5a635a8931f297249a3306f094a77d8df43435911a9157be9eb5de94"
BASE_ZIP = "38ceb2a093e6ffcd4ce91595ceedae917e18efcbf6f76377cf7ac9d4cf2e7279"
SMOKE_PLAN = "bd5e66be8865631774a1c735cac2182a17d3e989ca70148f32df4714d7e1f305"
WITNESS = "41b9c6830cdc00ea3186527b001a7a1d7579901d911b72e739bd863e28bcac38"
WITNESS_FILE = "b055fd4039409191a2774b5f63a50d27bcfad98d317dc79f99025567a47a168d"
PREFIX = "src/journeymap/"
ALLOWED = tuple(
    PREFIX + name
    for name in (
        "examples/route_update_live.py",
        "experiments/route_update_counted_plan.py",
        "experiments/route_update_counted_cost.py",
        "experiments/route_update_counted_audit.py",
        "experiments/route_update_live_audit.py",
        "experiments/route_update_limited_pilot.py",
        "experiments/route_update_pilot_compatibility.py",
    )
)


def source_bytes(value: JsonObject) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for name, text in value.items():
        if (
            (name != "pyproject.toml" and (not name.startswith(PREFIX) or not name.endswith(".py")))
            or ".." in name.split("/")
            or type(text) is not str
        ):
            raise ValueError("E04 compatibility source inventory")
        result[name] = text.encode("utf-8")
    return result


def semantic_probe(review: JsonObject) -> JsonObject:
    """Replay saved response strings through sessions; no runner or Provider invocation."""
    from journeymap.adapters.llm import strict_json
    from journeymap.adapters.route_update_count import count_wire
    from journeymap.adapters.route_update_live import CONFIG, decode
    from journeymap.experiments.route_update import demo_fixtures, move
    from journeymap.experiments.route_update_audit import check_first_inputs
    from journeymap.experiments.route_update_counted_session import CountedSession
    from journeymap.scenarios.route_update import CONDITIONS

    witness = obj(review["witness"])
    actual = CountedSession("smoke:initial", CONFIG, "live")
    try:
        prepared = actual.prepare(initial=True)
        counted = count_wire(obj(prepared["wire"]), obj(prepared["semantic_input"]), CONFIG)
        actual.finish_live(
            decode(obj(strict_json(cast(str, witness["generation_response_raw"]))), "live"),
            None,
            initial=True,
        )
        actual_result = actual.snapshot()
    finally:
        actual.close()
    if canonical_json(prepared) != canonical_json(witness["prepared"]) or (
        canonical_json(actual_result) != canonical_json(witness["result"])
    ):
        raise ValueError("E04 actual smoke semantics changed")
    evidence: list[JsonValue] = []

    def response(text: str) -> JsonObject:
        return {
            "id": "compatibility-replayed-response",
            "model": "gpt-6.1-sol",
            "status": "completed",
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 100,
                "total_tokens": 1100,
            },
            "service_tier": "default",
            "incomplete_details": None,
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        }

    wait = '{"action_type":"WAIT","payload":{"duration":1}}'
    for start_index, start in enumerate(("A", "B")):
        fixture = demo_fixtures()[start_index]
        first_inputs: list[JsonObject] = []
        for index, condition in enumerate(CONDITIONS):
            s = CountedSession("compat:" + start + ":" + condition, CONFIG, "recording-mock")
            try:
                s.reconstruct_prefix(
                    {"action_type": "MOVE", "payload": {"route_id": "S-to-" + start}},
                    {"scope": "compatibility-only; not service or sample"},
                )
                s.install_branch(condition)
                inputs: list[JsonValue] = []
                for step_index, text in enumerate(fixture.branches[index].outputs):
                    pending = s.prepare()
                    if step_index == 0:
                        first_inputs.append(pending)
                    inputs.append(
                        {
                            "semantic_input": pending["semantic_input"],
                            "generation_wire": pending["wire"],
                            "count_wire": count_wire(
                                obj(pending["wire"]), obj(pending["semantic_input"]), CONFIG
                            ),
                        }
                    )
                    s.finish_live(decode(response(text), "recording-mock"), None)
                    if s.stop != "RUNNING":
                        break
                evidence.append(
                    {"case": start + ":" + condition, "inputs": inputs, "result": s.snapshot()}
                )
            finally:
                s.close()
        check_first_inputs(first_inputs[0], first_inputs[1], notified=False)
        check_first_inputs(first_inputs[0], first_inputs[2], notified=True)
    for text in (wait, move("S-to-A"), "bad"):
        s = CountedSession("compat:prefix:" + digest({"text": text})[:8], CONFIG, "recording-mock")
        try:
            s.prepare(initial=True)
            s.finish_live(decode(response(text), "recording-mock"), None, initial=True)
            evidence.append({"case": "prefix:" + text, "result": s.snapshot()})
        finally:
            s.close()
    return {
        "actual_semantic_input": prepared["semantic_input"],
        "actual_generation_wire": prepared["wire"],
        "actual_count_wire": counted,
        "actual_result_sha256": digest(actual_result),
        "followup_cases": evidence,
        "method": (
            "saved-text session replay; shared Session/metrics; zero Provider/transport calls"
        ),
    }


def validate_compatibility(
    record: JsonObject,
    review: JsonObject,
    source: JsonObject,
    runtime: JsonObject,
    *,
    replay: bool = True,
) -> None:
    expected_keys = {
        "version",
        "authenticity_limit",
        "baseline_zip_sha256",
        "baseline_source_sha256",
        "current_source_sha256",
        "runtime",
        "smoke_plan_sha256",
        "witness_sha256",
        "witness_file_sha256",
        "witness_utf8",
        "baseline_sources",
        "changes",
        "protected_files",
        "baseline_semantics",
        "current_semantics",
        "record_sha256",
    }
    if set(record) != expected_keys or record["record_sha256"] != digest(
        {k: v for k, v in record.items() if k != "record_sha256"}
    ):
        raise ValueError("E04 compatibility record inventory/digest")
    expected: JsonObject = {
        "version": "experiment04-v3-smoke-v4-pilot-compatibility-1",
        "authenticity_limit": "UNSIGNED_INTERNAL_COMPATIBILITY; not service authentication",
        "baseline_zip_sha256": BASE_ZIP,
        "baseline_source_sha256": BASE_SOURCE,
        "smoke_plan_sha256": SMOKE_PLAN,
        "witness_sha256": WITNESS,
        "witness_file_sha256": WITNESS_FILE,
        "runtime": {"implementation": "CPython", "version": [3, 12, 14]},
    }
    if canonical_json({k: record.get(k) for k in expected}) != canonical_json(expected):
        raise ValueError("E04 compatibility pinned baseline/runtime mismatch")
    if canonical_json(runtime) != canonical_json(record["runtime"]):
        raise ValueError("E04 compatibility current runtime mismatch")
    text = record["witness_utf8"]
    if type(text) is not str or sha256(text.encode()).hexdigest() != WITNESS_FILE:
        raise ValueError("E04 compatibility original witness bytes mismatch")
    from journeymap.adapters.llm import strict_json
    from journeymap.experiments.route_update_counted_service import validate_review

    if canonical_json(obj(strict_json(text))) != canonical_json(review):
        raise ValueError("E04 compatibility witness binding mismatch")
    validate_review(review)
    w = obj(review["witness"])
    if review["witness_sha256"] != WITNESS or obj(w["plan"])["plan_sha256"] != SMOKE_PLAN:
        raise ValueError("E04 compatibility requires original actual smoke")
    if obj(obj(w["plan"])["source"])["working_source_sha256"] != BASE_SOURCE:
        raise ValueError("E04 compatibility smoke source mismatch")
    baseline = source_bytes(obj(record["baseline_sources"]))
    if logical_source_sha256(baseline) != BASE_SOURCE:
        raise ValueError("E04 compatibility baseline source inventory mismatch")
    current = baseline.copy()
    changed: set[str] = set()
    for row in rows(record["changes"]):
        if set(row) != {"path", "before_sha256", "after_sha256", "after_utf8", "reason"}:
            raise ValueError("E04 compatibility change inventory")
        name = cast(str, row["path"])
        if name not in ALLOWED or name in changed or type(row["after_utf8"]) is not str:
            raise ValueError("E04 protected/unreviewed/duplicate source change")
        before = sha256(baseline[name]).hexdigest() if name in baseline else None
        after = row["after_utf8"].encode()
        if row["before_sha256"] != before or row["after_sha256"] != sha256(after).hexdigest():
            raise ValueError("E04 compatibility change hash mismatch")
        if (
            before == row["after_sha256"]
            or row["reason"] != "v4 plan/cost/approval/compatibility/audit/CLI dispatch only"
        ):
            raise ValueError("E04 compatibility change reason/no-op")
        current[name] = after
        changed.add(name)
    if changed != set(ALLOWED):
        raise ValueError("E04 compatibility incomplete reviewed change list")
    protected = sorted(set(baseline) - set(ALLOWED))
    if record["protected_files"] != protected:
        raise ValueError("E04 compatibility incomplete protected inventory")
    if logical_source_sha256(current) != record["current_source_sha256"] or (
        record["current_source_sha256"] != source["working_source_sha256"]
    ):
        raise ValueError("E04 compatibility current full source mismatch")
    if canonical_json(record["baseline_semantics"]) != canonical_json(record["current_semantics"]):
        raise ValueError("E04 compatibility before/after semantics mismatch")
    if replay and canonical_json(record["current_semantics"]) != canonical_json(
        semantic_probe(review)
    ):
        raise ValueError("E04 compatibility reconstructed input/action/evaluation mismatch")


def create_compatibility(
    baseline: JsonObject,
    witness_utf8: str,
    baseline_semantics: JsonObject,
    current_sources: JsonObject,
    source: JsonObject,
    runtime: JsonObject,
) -> JsonObject:
    """Issue a review record from captured source and independently executed old replay."""
    from journeymap.adapters.llm import strict_json

    review = obj(strict_json(witness_utf8))
    before, after = source_bytes(baseline), source_bytes(current_sources)
    if set(before) - set(after):
        raise ValueError("E04 compatibility deleted source")
    changes: list[JsonValue] = []
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            changes.append(
                {
                    "path": name,
                    "before_sha256": sha256(before[name]).hexdigest() if name in before else None,
                    "after_sha256": sha256(after[name]).hexdigest(),
                    "after_utf8": after[name].decode(),
                    "reason": "v4 plan/cost/approval/compatibility/audit/CLI dispatch only",
                }
            )
    value: JsonObject = {
        "version": "experiment04-v3-smoke-v4-pilot-compatibility-1",
        "authenticity_limit": "UNSIGNED_INTERNAL_COMPATIBILITY; not service authentication",
        "baseline_zip_sha256": BASE_ZIP,
        "baseline_source_sha256": BASE_SOURCE,
        "current_source_sha256": source["working_source_sha256"],
        "runtime": runtime,
        "smoke_plan_sha256": SMOKE_PLAN,
        "witness_sha256": WITNESS,
        "witness_file_sha256": WITNESS_FILE,
        "witness_utf8": witness_utf8,
        "baseline_sources": baseline,
        "changes": changes,
        "protected_files": cast(list[JsonValue], sorted(set(before) - set(ALLOWED))),
        "baseline_semantics": baseline_semantics,
        "current_semantics": semantic_probe(review),
    }
    value["record_sha256"] = digest(value)
    validate_compatibility(value, review, source, runtime)
    return value
