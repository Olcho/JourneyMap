"""Paired inputs, information boundaries, failure denominators and offline engine checks."""

import socket
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from journeymap.adapters import life_http
from journeymap.adapters.life_http import paid_body, paid_request
from journeymap.adapters.life_llm import PROMPT, life_body, life_request
from journeymap.adapters.life_responses import LifeExecutionPlan, synthetic_response
from journeymap.adapters.llm import strict_json
from journeymap.core.canonical import JsonObject, canonical_json, clone_json_object, state_digest
from journeymap.examples.life_reasoning_probe import read_object, save
from journeymap.experiments import life_reasoning_probe as probe
from journeymap.experiments.life import LifeSession
from journeymap.scenarios.life import LifeConfig


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("offline probe must not access network, keys or paid admission")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    connect = sqlite3.connect

    def memory_only(database: str, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        if database != ":memory:":
            raise AssertionError("offline probe must not open a persistent database")
        return cast(sqlite3.Connection, connect(database, *args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", memory_only)
    monkeypatch.setattr(life_http, "real_credential", forbidden)
    monkeypatch.setattr(life_http, "real_connection", forbidden)
    monkeypatch.setattr(life_http, "admit_provider", forbidden)


@pytest.fixture(scope="module")
def corpus() -> JsonObject:
    # Preparation itself does not import a Provider factory or read credentials.
    return probe.prepare()


def test_exact_baseline_and_single_factor_contrasts() -> None:
    session, observation = probe.build_case(probe.cases()[0])
    try:
        high = probe.probe_body(observation, "high_baseline")
        medium = probe.probe_body(observation, "medium_baseline")
        structured = probe.probe_body(observation, "medium_structured")
        assert high == paid_body(paid_request(observation, LifeExecutionPlan()))
        assert medium == high | {"reasoning": {"effort": "medium"}}
        assert {k: v for k, v in structured.items() if k != "input"} == {
            k: v for k, v in medium.items() if k != "input"
        }
        assert str(structured["input"]).replace(probe.STRUCTURE, "", 1) == medium["input"]
        assert str(high["input"]).startswith(PROMPT)
        # Historical live gate still rejects the new condition.
        with pytest.raises(ValueError, match="invalid life model parameters"):
            life_body(
                replace(
                    life_request(observation, 8192),
                    parameters={
                        "max_output_tokens": 8192,
                        "reasoning": {"effort": "medium"},
                    },
                )
            )
        with pytest.raises(ValueError, match="unsupported execution plan"):
            LifeExecutionPlan(reasoning="medium")
    finally:
        session.kernel.close()


def test_hidden_future_and_remote_private_resources_do_not_change_input() -> None:
    base = LifeSession(LifeConfig(horizon=24))
    changed = LifeSession(LifeConfig(horizon=24, closure_tick=2, employer_money=0, flour=0))
    try:
        a = base.application.game_for("ada").observe()
        b = changed.application.game_for("ada").observe()
        assert state_digest(base.kernel.state_snapshot) != state_digest(
            changed.kernel.state_snapshot
        )
        assert a.content == b.content
        for condition in probe.CONDITIONS:
            assert probe.probe_body(a, condition) == probe.probe_body(b, condition)
        text = canonical_json(probe.probe_body(a, "medium_structured"))
        for research_only in ("case_id", "world_digest", "fixture_origin", "closure_tick"):
            assert research_only not in text
    finally:
        base.kernel.close()
        changed.kernel.close()


def test_corpus_pairing_and_repetitions(corpus: JsonObject) -> None:
    requests = probe._rows(corpus["requests"])
    assert len(requests) == len(probe.cases()) * len(probe.CONDITIONS) * probe.REPETITIONS
    assert corpus["authorized"] is False and corpus["live_capable"] is False
    assert len({r["request_id"] for r in requests}) == len(requests)
    for case in probe.cases():
        group = [r for r in requests if r["case_id"] == case.case_id]
        for condition in probe.CONDITIONS:
            repeated = [r for r in group if r["condition"] == condition]
            assert len(repeated) == probe.REPETITIONS
            assert repeated[0]["body"] == repeated[1]["body"]


def packet(raw: str | None, *, status: int | None = 200, elapsed: int = 50) -> JsonObject:
    return {"raw_response": raw, "http_status": status, "elapsed_ms": elapsed}


def test_incomplete_usage_is_kept_without_parsing_or_double_counting() -> None:
    document = cast(JsonObject, strict_json(synthetic_response(probe.WAIT, status="incomplete")))
    result = probe._parse_packet(packet(canonical_json(document)))
    assert result["classification"] == "INCOMPLETE"
    # 1000 input, 200 cached, 100 output (including 60 reasoning), nano-USD.
    assert result["known_cost_nano_usd"] == 800 * 750 + 200 * 75 + 100 * 4500
    assert result["candidate_text"] is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "TRANSPORT_UNCERTAIN"),
        ("not-json", "INVALID_RESPONSE"),
        ('{"usage":null}', "USAGE_UNAVAILABLE"),
        ('{"usage":{},"usage":{}}', "INVALID_RESPONSE"),
        ("[" * 2000 + "]" * 2000, "INVALID_RESPONSE"),
        (synthetic_response("no", refusal=True), "REFUSAL"),
    ],
)
def test_response_failures_are_not_ready(raw: str | None, expected: str) -> None:
    assert probe._parse_packet(packet(raw))["classification"] == expected


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("model", "another-model", "IDENTITY_MISMATCH"),
        ("service_tier", "priority", "IDENTITY_MISMATCH"),
        ("status", "failed", "RESPONSE_FAILED"),
        ("id", "", "INVALID_RESPONSE_ID"),
    ],
)
def test_response_identity_and_status(field: str, value: str, expected: str) -> None:
    document = cast(JsonObject, strict_json(synthetic_response(probe.WAIT)))
    document[field] = value
    result = probe._parse_packet(packet(canonical_json(document)))
    assert result["classification"] == expected
    assert result["known_cost_nano_usd"] is not None


def test_timeout_boundary_and_http_error_keep_usage() -> None:
    raw = synthetic_response(probe.WAIT)
    assert probe._parse_packet(packet(raw, elapsed=120_000))["classification"] == "READY"
    assert probe._parse_packet(packet(raw, elapsed=120_001))["classification"] == "TIMEOUT"
    result = probe._parse_packet(packet(raw, status=500))
    assert result["classification"] == "HTTP_ERROR"
    assert result["known_cost_nano_usd"] is not None


@pytest.mark.parametrize(
    "output,reasoning,total,expected",
    [
        (100, 101, 1100, "INVALID_USAGE"),
        (True, 0, 1001, "INVALID_USAGE"),
        (100, 60, 999, "INVALID_USAGE"),
        (8193, 8193, 9193, "USAGE_EXCEEDS_LIMIT"),
    ],
)
def test_usage_validation(output: Any, reasoning: int, total: int, expected: str) -> None:
    doc = cast(JsonObject, strict_json(synthetic_response(probe.WAIT)))
    usage = probe._object(doc["usage"])
    usage.update(
        {
            "output_tokens": output,
            "total_tokens": total,
            "output_tokens_details": {"reasoning_tokens": reasoning},
        }
    )
    assert probe._parse_packet(packet(canonical_json(doc)))["classification"] == expected


def test_missing_rows_remain_unknown_cost_and_denominator(corpus: JsonObject) -> None:
    packets = probe.mock_packets(corpus)
    packets["records"] = []
    result = probe.evaluate(corpus, packets)
    for summary in probe._object(result["conditions"]).values():
        s = probe._object(summary)
        assert s["scheduled"] == len(probe.cases()) * probe.REPETITIONS
        assert s["received"] == 0 and s["usage_known_count"] == 0
        assert s["total_cost_nano_usd"] is None
        assert s["cost_per_completed_action_nano_usd"] is None
        assert s["24h_completion_rate"] is None


def test_mock_engine_branches_and_data_only_re_evaluation(corpus: JsonObject) -> None:
    packets = probe.mock_packets(corpus)
    result = probe.evaluate(corpus, packets)
    summaries = probe._object(result["conditions"])
    assert (
        summaries["high_baseline"] == summaries["medium_baseline"] == summaries["medium_structured"]
    )
    statuses = probe._object(probe._object(summaries["high_baseline"])["engine_status_counts"])
    for status in ("SUCCEEDED", "FAILED", "REJECTED", "PENDING_AT_HORIZON", "INVALID_CANDIDATE"):
        assert cast(int, statuses[status]) > 0
    assert result["token_saving_effect_established"] is False
    assert result["authorized_live_execution_verified"] is False
    assert probe.evaluate(corpus, packets) == result


@pytest.mark.parametrize(
    "mutation", ["duplicate", "unknown", "wrong_body", "duplicate_response", "bad_time"]
)
def test_packet_identity_rejections(corpus: JsonObject, mutation: str) -> None:
    packets = probe.mock_packets(corpus)
    records = probe._rows(packets["records"])
    if mutation == "duplicate":
        records.append(records[0])
        packets["records"] = list(records)
    elif mutation == "unknown":
        records[0]["request_id"] = "unknown"
    elif mutation == "wrong_body":
        records[0]["body_digest"] = "0" * 64
    elif mutation == "duplicate_response":
        records[1]["raw_response"] = records[0]["raw_response"]
    else:
        records[0]["elapsed_ms"] = True
    with pytest.raises(ValueError):
        probe._validate_packets(packets, corpus)


def test_resealed_corpus_tampering_is_rejected(corpus: JsonObject) -> None:
    changed = clone_json_object(corpus)
    row = probe._rows(changed["requests"])[0]
    body = probe._object(row["body"])
    body["input"] = str(body["input"]) + " secret answer"
    row["body_digest"] = state_digest(body)
    changed["corpus_digest"] = state_digest(
        {k: v for k, v in changed.items() if k != "corpus_digest"}
    )
    with pytest.raises(ValueError, match="corpus differs"):
        probe.evaluate(changed, probe.mock_packets(changed))


def test_unknown_condition_and_actor_rejected() -> None:
    session, observation = probe.build_case(probe.cases()[0])
    try:
        with pytest.raises(ValueError):
            probe.probe_body(observation, "high_structured")
        with pytest.raises(ValueError):
            probe.probe_body(session.application.game_for("bert").observe(), "medium_baseline")
    finally:
        session.kernel.close()


def test_saving_does_not_overwrite_existing_artifacts(tmp_path: Path) -> None:
    target = tmp_path / "probe"
    save(target, {"corpus.json": {"immutable": True}})
    before = (target / "corpus.json").read_bytes()
    with pytest.raises(FileExistsError):
        save(target, {"corpus.json": {"immutable": False}})
    assert (target / "corpus.json").read_bytes() == before
    assert read_object(target / "corpus.json") == {"immutable": True}
