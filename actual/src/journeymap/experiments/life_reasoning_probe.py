"""Data-only, paired life decision probe. No Provider, credentials or live runner.

Fixture worlds belong to the researcher. Only life_request(Observation) reaches
the proposed wire. Engine success is feasibility evidence, never an optimality
label, and these one-decision branches are not 24-hour behavioral trajectories.
"""

from collections import Counter
from dataclasses import asdict, dataclass, replace
from typing import cast

from journeymap.adapters.life_admission import source_identity
from journeymap.adapters.life_llm import PROMPT, life_body, life_candidate, life_request
from journeymap.adapters.life_responses import MODEL, Usage, response_text, synthetic_response
from journeymap.adapters.llm import Record, strict_json
from journeymap.application.concurrent_replay import LiveInput
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json, state_digest
from journeymap.core.handlers import ActionValidationError
from journeymap.core.observations import Observation
from journeymap.experiments.life import LifeSession, json_data, people
from journeymap.scenarios.life import LifeConfig

VERSION = "life-reasoning-probe-offline-1"
REPETITIONS = 2
STRUCTURE = """Decision procedure for this single action: identify your current needs and
resources, check relevant observed opportunities and your previous public result,
then choose one feasible next action toward managing your needs and resources.
Keep deliberation focused on this decision; do not enumerate speculative futures
or repeatedly recheck an unchanged choice. The task and available information are
unchanged. Return only the original action JSON, without analysis or a plan.
"""
CONDITIONS = ("high_baseline", "medium_baseline", "medium_structured")


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    tick: int
    config: LifeConfig
    prefix: tuple[tuple[int, str], ...] = ()


def candidate(action: str, payload: JsonObject) -> str:
    return canonical_json({"action_type": action, "payload": payload})


MOVE_HOME = candidate("MOVE", {"route_id": "home-square"})
MOVE_SHOP = candidate("MOVE", {"route_id": "square-shop"})
WORK = candidate("WORK", {"job_id": "bake"})
WAIT = candidate("WAIT", {"duration": 1})
TO_SHOP = ((0, MOVE_HOME), (1, MOVE_SHOP))


def cases() -> tuple[Case, ...]:
    config = LifeConfig(horizon=24)
    return (
        Case("home_0", 0, config),
        Case("shop_3", 3, config, TO_SHOP),
        Case("after_work_6", 6, config, (*TO_SHOP, (3, WORK))),
        Case("after_work_9", 9, config, (*TO_SHOP, (3, WORK), (6, WORK))),
        Case("stockout_3", 3, replace(config, bread=0), TO_SHOP),
        Case("no_money_3", 3, replace(config, worker_money=0), TO_SHOP),
        Case("no_wages_3", 3, replace(config, employer_money=0), TO_SHOP),
        Case("no_materials_3", 3, replace(config, flour=0), TO_SHOP),
        Case("closed_route_1", 1, replace(config, closure_tick=1), ((0, MOVE_HOME),)),
        Case("closing_route_1", 1, replace(config, closure_tick=2), ((0, MOVE_HOME),)),
        Case("late_home_18", 18, config, ((0, candidate("WAIT", {"duration": 18})),)),
        Case(
            "rejected_purchase_3",
            3,
            config,
            (*TO_SHOP, (3, candidate("BUY", {"offer_id": "bread-sale", "quantity": 100}))),
        ),
    )


def _observe(session: LifeSession) -> Observation:
    observation = session.application.game_for("ada").observe()
    session.observations.append(observation)
    session.inputs.append(
        LiveInput(
            "observe",
            observation.simulation_time,
            "ada",
            observation_id=observation.observation_id,
            observation_digest=observation.content_digest,
        )
    )
    return observation


def build_case(case: Case) -> tuple[LifeSession, Observation]:
    """Fresh deterministic branch; peers use RULE, Ada has a literal prefix.

    This does not reconstruct either historical live run. Catalog names, private
    fixture config, prefix and world digests are research-side metadata only.
    """
    if case not in cases():
        raise ValueError("unknown probe fixture")
    session = LifeSession(case.config)
    del session.controllers["ada"]
    try:
        for tick, text in case.prefix:
            session.run_until(tick)
            observation = _observe(session)
            _, request = life_candidate(text, observation)
            receipt = session.application.game_for("ada").submit(request)
            session.inputs.append(
                LiveInput("submit", tick, "ada", request=request, receipt=receipt)
            )
        session.run_until(case.tick)
        if any(frame.request.actor_id == "ada" for frame in session.kernel.in_flight):
            raise ValueError("probe actor is busy")
        return session, _observe(session)
    except BaseException:
        session.kernel.close()
        raise


def probe_body(observation: Observation, condition: str) -> JsonObject:
    """Export a proposed body, never a live ProviderRequest or authorization.

    Keep the historical high-only builders and approval validation unchanged.
    A future paid probe needs its own reviewed composition and consumed budget.
    """
    if condition not in CONDITIONS or observation.actor_id != "ada":
        raise ValueError("unknown probe condition or actor")
    request = replace(life_request(observation, 8192, 24), model=MODEL)
    body = life_body(request)
    body["service_tier"] = "default"
    if condition != "high_baseline":
        body["reasoning"] = {"effort": "medium"}
    if condition == "medium_structured":
        body["input"] = PROMPT + STRUCTURE + str(body["input"])[len(PROMPT) :]
    if len(canonical_json(body).encode("utf-8")) > 65_536:
        raise ValueError("probe input too large")
    return body


def prepare() -> JsonObject:
    """Versioned corpus plus a rotated, fixed execution order (not shuffled RNG)."""
    fixtures: list[JsonValue] = []
    requests: list[JsonValue] = []
    for index, case in enumerate(cases()):
        session, observation = build_case(case)
        try:
            fixtures.append(
                {
                    "case_id": case.case_id,
                    "fixture": json_data(asdict(case)),
                    "observation": json_data(asdict(observation)),
                    "world_digest": state_digest(session.kernel.state_snapshot),
                }
            )
            for repeat in range(REPETITIONS):
                pivot = (index + repeat) % len(CONDITIONS)
                for condition in CONDITIONS[pivot:] + CONDITIONS[:pivot]:
                    body = probe_body(observation, condition)
                    requests.append(
                        {
                            "request_id": f"{case.case_id}:{condition}:{repeat + 1}",
                            "case_id": case.case_id,
                            "condition": condition,
                            "repeat": repeat + 1,
                            "body": body,
                            "body_digest": state_digest(body),
                        }
                    )
        finally:
            session.kernel.close()
    payload: JsonObject = {
        "version": VERSION,
        "authorized": False,
        "live_capable": False,
        "evidence_kind": "synthetic-engine-fixtures",
        "source_identity": source_identity(),
        "fixture_origin": "RULE peers and literal Ada prefixes; not historical live states",
        "conditions": list(CONDITIONS),
        "repetitions": REPETITIONS,
        "fixtures": fixtures,
        "requests": requests,
        "price_nano_usd_per_token": {"input": 750, "cached_input": 75, "output": 4500},
    }
    return payload | {"corpus_digest": state_digest(payload)}


def _object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return value


def _rows(value: JsonValue) -> list[JsonObject]:
    if not isinstance(value, list):
        raise ValueError("expected array")
    return [_object(row) for row in value]


def _parse_packet(packet: JsonObject) -> JsonObject:
    """Retain failures and usage independently. Missing usage is never zero cost."""
    result: JsonObject = {
        "classification": "INVALID_RESPONSE",
        "usage": None,
        "known_cost_nano_usd": None,
        "candidate_text": None,
    }
    raw = packet["raw_response"]
    if raw is None:
        return result | {"classification": "TRANSPORT_UNCERTAIN"}
    assert isinstance(raw, str)
    try:
        if len(raw.encode("utf-8")) > 2_000_000:
            return result | {"classification": "RESPONSE_TOO_LARGE"}
        document = strict_json(raw)
        if not isinstance(document, dict):
            return result
        try:
            usage = Usage.parse(document.get("usage"))
            result["usage"] = json_data(asdict(usage))
            result["known_cost_nano_usd"] = (
                (usage.input_tokens - usage.cached_tokens) * 750
                + usage.cached_tokens * 75
                + usage.output_tokens * 4500
            )
        except ValueError as error:
            return result | {"classification": str(error)}
        if packet["http_status"] != 200:
            return result | {"classification": "HTTP_ERROR"}
        if usage.output_tokens > 8192 or usage.input_tokens > 400_000:
            return result | {"classification": "USAGE_EXCEEDS_LIMIT"}
        if document.get("model") != MODEL or document.get("service_tier") != "default":
            return result | {"classification": "IDENTITY_MISMATCH"}
        if type(document.get("id")) is not str or not document["id"]:
            return result | {"classification": "INVALID_RESPONSE_ID"}
        if document.get("status") == "incomplete":
            return result | {"classification": "INCOMPLETE"}
        if document.get("status") != "completed":
            return result | {"classification": "RESPONSE_FAILED"}
        text, refusal = response_text(document)
        if refusal:
            return result | {"classification": "REFUSAL"}
        if len(text.encode("utf-8")) > 16_384:
            return result | {"classification": "OUTPUT_TOO_LARGE"}
        elapsed = packet["elapsed_ms"]
        if elapsed is not None and cast(int, elapsed) > 120_000:
            return result | {"classification": "TIMEOUT"}
        return result | {"classification": "READY", "candidate_text": text}
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return result


def _engine_result(case: Case, text: str) -> JsonObject:
    session, observation = build_case(case)
    try:
        before = people(session.kernel.state_snapshot)["ada"]
        try:
            decoded, request = life_candidate(text, observation)
        except (ValueError, TypeError, RecursionError, ActionValidationError):
            return {"status": "INVALID_CANDIDATE", "candidate": None}
        receipt = session.application.game_for("ada").submit(request)
        session.inputs.append(
            LiveInput(
                "submit",
                case.tick,
                "ada",
                request=request,
                receipt=receipt,
            )
        )
        if receipt.status == "ACCEPTED":
            assert receipt.completes_at is not None
            session.run_until(min(receipt.completes_at, case.config.horizon))
        final = session.application.game_for("ada").result(request.action_request_id)
        assert final is not None
        status = "PENDING_AT_HORIZON" if final.status == "ACCEPTED" else final.status
        return {
            "status": status,
            "candidate": decoded,
            "receipt": json_data(asdict(final)),
            "before": before,
            "after": people(session.kernel.state_snapshot)["ada"],
            "final_tick": session.kernel.simulation_time,
            "final_world_digest": state_digest(session.kernel.state_snapshot),
            "completion_is_behavioral_success": False,
        }
    finally:
        session.kernel.close()


def _validate_packets(packets: JsonObject, corpus: JsonObject) -> list[JsonObject]:
    if set(packets) != {"version", "corpus_digest", "evidence_kind", "records"}:
        raise ValueError("invalid packet bundle")
    if packets["version"] != VERSION or packets["corpus_digest"] != corpus["corpus_digest"]:
        raise ValueError("packet binding mismatch")
    if packets["evidence_kind"] not in {"synthetic", "imported-unverified"}:
        raise ValueError("unsupported evidence kind; this tool cannot certify live evidence")
    rows = _rows(packets["records"])
    requests = {str(row["request_id"]): row for row in _rows(corpus["requests"])}
    seen: set[str] = set()
    response_ids: set[str] = set()
    for packet in rows:
        if set(packet) != {
            "request_id",
            "body_digest",
            "http_status",
            "raw_response",
            "elapsed_ms",
        }:
            raise ValueError("invalid packet fields")
        rid = packet["request_id"]
        if type(rid) is not str or rid not in requests or rid in seen:
            raise ValueError("unknown or duplicate request")
        seen.add(rid)
        if packet["body_digest"] != requests[rid]["body_digest"]:
            raise ValueError("response/request binding mismatch")
        status, raw, elapsed = packet["http_status"], packet["raw_response"], packet["elapsed_ms"]
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise ValueError("invalid HTTP status")
        if raw is not None and type(raw) is not str:
            raise ValueError("invalid raw response")
        if elapsed is not None and (type(elapsed) is not int or elapsed < 0):
            raise ValueError("invalid elapsed time")
        # Reusing an apparent provider response across paired slots is not a repeat.
        try:
            document = strict_json(raw) if isinstance(raw, str) and len(raw) <= 2_000_000 else None
        except (ValueError, TypeError, RecursionError):
            document = None
        if isinstance(document, dict) and isinstance(document.get("id"), str):
            response_id = str(document["id"])
            if response_id in response_ids:
                raise ValueError("duplicate response id")
            response_ids.add(response_id)
    return rows


def evaluate(corpus: JsonObject, packets: JsonObject) -> JsonObject:
    """Read data and branch local engines only; no transport injection surface.

    Regenerate the frozen corpus to reject resealed changes to model inputs or
    fixtures. Missing rows remain in the denominator with unknown usage/cost.
    Imported records are unverified data, not proof of authorized API execution.
    """
    if canonical_json(corpus) != canonical_json(prepare()):
        raise ValueError("corpus differs from current frozen source/fixtures")
    received = {str(p["request_id"]): p for p in _validate_packets(packets, corpus)}
    catalog = {case.case_id: case for case in cases()}
    rows: list[JsonValue] = []
    engine_cache: dict[tuple[str, str], Record] = {}
    for request in _rows(corpus["requests"]):
        rid = str(request["request_id"])
        row: JsonObject = {
            "request_id": rid,
            "case_id": request["case_id"],
            "condition": request["condition"],
            "repeat": request["repeat"],
            "classification": "MISSING",
            "usage": None,
            "known_cost_nano_usd": None,
            "elapsed_ms": None,
            "engine": None,
        }
        packet = received.get(rid)
        if packet is not None:
            row.update(_parse_packet(packet))
            row["elapsed_ms"] = packet["elapsed_ms"]
            if row["classification"] == "READY":
                key = (str(request["case_id"]), str(row["candidate_text"]))
                if key not in engine_cache:
                    engine_cache[key] = Record.capture(_engine_result(catalog[key[0]], key[1]))
                row["engine"] = engine_cache[key].data
        rows.append(row)
    summaries: JsonObject = {}
    for condition in CONDITIONS:
        group = [r for r in _rows(rows) if r["condition"] == condition]
        known = [r for r in group if r["usage"] is not None]
        cost = sum(cast(int, r["known_cost_nano_usd"]) for r in known)
        engines = [_object(r["engine"]) for r in group if r["engine"] is not None]
        completed = sum(e["status"] == "SUCCEEDED" for e in engines)
        measured = [cast(int, r["elapsed_ms"]) for r in group if r["elapsed_ms"] is not None]
        summaries[condition] = {
            "scheduled": len(group),
            "received": sum(r["classification"] != "MISSING" for r in group),
            "classification_counts": dict(Counter(str(r["classification"]) for r in group)),
            "engine_status_counts": dict(Counter(str(e["status"]) for e in engines)),
            "action_counts": dict(
                Counter(
                    str(_object(e["candidate"])["action_type"])
                    for e in engines
                    if e["candidate"] is not None
                )
            ),
            "completed_actions": completed,
            "usage_known_count": len(known),
            "known_token_totals": {
                key: sum(cast(int, _object(r["usage"])[key]) for r in known)
                for key in ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens")
            },
            "known_cost_nano_usd": cost,
            "total_cost_nano_usd": cost if len(known) == len(group) else None,
            "cost_per_completed_action_nano_usd": cost / completed
            if completed and len(known) == len(group)
            else None,
            "latency_known_count": len(measured),
            "mean_recorded_latency_ms": sum(measured) / len(measured) if measured else None,
            "behavioral_success_rate": None,
            "24h_completion_rate": None,
        }
    return {
        "version": VERSION,
        "corpus_digest": corpus["corpus_digest"],
        "packets_digest": state_digest(packets),
        "evidence_kind": packets["evidence_kind"],
        "authorized_live_execution_verified": False,
        "scope": "one-decision feasibility; no optimal-action labels or life-completion claim",
        "rows": rows,
        "conditions": summaries,
        "token_saving_effect_established": False,
    }


def mock_packets(corpus: JsonObject) -> JsonObject:
    """Literal identical fixtures per condition, never predictions or token estimates."""
    records: list[JsonValue] = []
    for request in _rows(corpus["requests"]):
        case_id = request["case_id"]
        raw = synthetic_response(WAIT)
        document = _object(strict_json(raw))
        if case_id == "after_work_6":
            document["status"] = "incomplete"
            document["output"] = []
            document["incomplete_details"] = {"reason": "max_output_tokens"}
            document["usage"] = {
                "input_tokens": 1000,
                "output_tokens": 8192,
                "total_tokens": 9192,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 8192},
            }
        elif case_id == "no_money_3":
            document = _object(
                strict_json(
                    synthetic_response(candidate("BUY", {"offer_id": "bread-sale", "quantity": 1}))
                )
            )
        elif case_id == "closing_route_1":
            document = _object(strict_json(synthetic_response(MOVE_SHOP)))
        elif case_id == "rejected_purchase_3":
            document = _object(
                strict_json(
                    synthetic_response('{"action_type":"WAIT","payload":{"duration":true}}')
                )
            )
        elif case_id == "late_home_18":
            document = _object(strict_json(synthetic_response(candidate("REST", {"duration": 10}))))
        document["id"] = "synthetic:" + str(request["request_id"])
        records.append(
            {
                "request_id": request["request_id"],
                "body_digest": request["body_digest"],
                "http_status": 200,
                "raw_response": canonical_json(document),
                "elapsed_ms": 50,
            }
        )
    return {
        "version": VERSION,
        "corpus_digest": corpus["corpus_digest"],
        "evidence_kind": "synthetic",
        "records": records,
    }
