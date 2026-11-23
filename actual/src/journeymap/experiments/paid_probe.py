"""Independent paired decisions with durable admission; no retry or live world loop."""

import json
import math
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import cast

from journeymap.adapters.life_http import Connection, real_connection, real_credential
from journeymap.adapters.llm import strict_json
from journeymap.adapters.probe_diagnostics import (
    TransportDiagnostic,
    exception_diagnostic,
    exception_error,
)
from journeymap.adapters.probe_execution import (
    CONTINUE,
    PROTOCOL,
    Ledger,
    ProbeApproval,
    ProbePlan,
    binding,
    classify,
    object_,
    rows,
    same,
)
from journeymap.adapters.probe_journal import ProbeJournal, read_journal, reduce_trace
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json, clone_json_object
from journeymap.experiments import life_reasoning_probe as probe


@dataclass(frozen=True, slots=True)
class WireReply:
    http_status: int | None
    raw_response: str | None
    elapsed_ms: int
    error: str | None = None
    diagnostic: TransportDiagnostic | None = None


@dataclass(slots=True)
class RecordingProbeTransport:
    replies: tuple[WireReply | Exception, ...]
    bodies: list[JsonObject] = field(default_factory=list)
    claimed: bool = False

    def prepare(self) -> None:
        pass

    def send(self, body: JsonObject) -> WireReply:
        self.bodies.append(clone_json_object(body))
        reply = self.replies[len(self.bodies) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply


class HTTPProbeTransport:
    """Fixed HTTPS endpoint; connection/key work only after journal admission.

    Synchronous read timeout plus total deadline checks; not a hard-kill timer.
    A fully received late response is retained for accounting, never acted on.
    """

    def __init__(
        self,
        *,
        credential: Callable[[], str] | None = None,
        connection: Callable[[str, float], Connection] | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.credential = credential if credential is not None else real_credential
        self.connection = connection if connection is not None else real_connection
        self.clock = clock
        self._key: str | None = None
        self.claimed = False

    def prepare(self) -> None:
        if self._key is None:
            key = self.credential()
            if type(key) is not str or not key or "\r" in key or "\n" in key:
                raise ValueError("credential unavailable")
            self._key = key

    def send(self, body: JsonObject) -> WireReply:
        if self._key is None:
            raise PermissionError("credential not prepared")
        key, encoded = self._key, canonical_json(body)
        started = self.clock()
        conn: Connection | None = None
        status: int | None = None
        stage = "REQUEST_SCAN"

        def elapsed() -> int:
            seconds = self.clock() - started
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError("invalid monotonic clock")
            return math.ceil(seconds * 1000)

        try:
            if key in encoded:
                return WireReply(
                    None,
                    None,
                    elapsed(),
                    "SECRET_ECHO",
                    TransportDiagnostic(stage, "NONE", "SECRET_ECHO"),
                )
            stage = "CONNECTION_OBJECT"
            conn = self.connection("api.openai.com", 120.0)
            stage = "REQUEST"  # HTTPSConnection may perform DNS/TCP/TLS inside request().
            conn.request(
                "POST",
                "/v1/responses",
                encoded.encode(),
                {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            )
            stage = "RESPONSE_HEADERS"
            response = conn.getresponse()
            status = response.status
            stage = "BODY_READ"
            parts: list[bytes] = []
            size = 0
            while True:
                part = response.read1(min(65_536, 2_000_001 - size))
                if not part:
                    break
                parts.append(part)
                size += len(part)
                if size > 2_000_000:
                    return WireReply(
                        status,
                        None,
                        elapsed(),
                        "RESPONSE_TOO_LARGE",
                        TransportDiagnostic(stage, "NONE", "SIZE_LIMIT"),
                    )
                if response.isclosed():
                    break
                if elapsed() > 120_000:
                    return WireReply(
                        status,
                        None,
                        elapsed(),
                        "TIMEOUT",
                        TransportDiagnostic(stage, "NONE", "DEADLINE_PARTIAL"),
                    )
            completed_ms = elapsed()
            stage = "FRAMING_CHECK"
            # read1() closes on premature EOF without raising for Content-Length.
            # A parseable JSON prefix is not evidence of a complete HTTP message.
            if getattr(response, "length", None) not in (None, 0) or (
                getattr(response, "chunked", False)
                and (getattr(response, "chunk_left", None) is not None or not response.isclosed())
            ):
                return WireReply(
                    status,
                    None,
                    completed_ms,
                    "TRANSPORT_UNCERTAIN",
                    TransportDiagnostic(stage, "NONE", "FRAMING_INCOMPLETE"),
                )
            stage = "DECODE_JSON"
            raw = b"".join(parts).decode("utf-8")
            # Invalid JSON is withheld; normalized JSON scanning also catches escaped keys.
            doc = strict_json(raw)
            if not isinstance(doc, dict):
                return WireReply(
                    status,
                    None,
                    completed_ms,
                    "INVALID_RESPONSE",
                    TransportDiagnostic(stage, "NONE", "INVALID_RESPONSE", True),
                )
            stage = "SECRET_SCAN"
            if key in raw or json.dumps(key)[1:-1] in raw or key in canonical_json(doc):
                return WireReply(
                    status,
                    None,
                    completed_ms,
                    "SECRET_ECHO",
                    TransportDiagnostic(stage, "NONE", "SECRET_ECHO", True),
                )
            return WireReply(
                status,
                raw,
                completed_ms,
                diagnostic=TransportDiagnostic(
                    stage, "NONE", "LATE_COMPLETE" if completed_ms > 120_000 else "COMPLETE", True
                ),
            )
        except Exception as exc:
            diagnostic = exception_diagnostic(stage, exc)
            return WireReply(status, None, elapsed(), exception_error(diagnostic), diagnostic)
        finally:
            if conn is not None:
                with suppress(Exception):
                    conn.close()


def proposal(run_id: str, registry: Path) -> JsonObject:
    corpus = probe.prepare()
    return {
        "authorized": False,
        "protocol": PROTOCOL,
        "corpus": corpus,
        "binding": binding(run_id, registry, corpus),
        "budget_note": "USD 1.00 candidate cap; 72 slots may not fit. Not approval.",
        "max_output_only_nano_usd": 72 * 8192 * 4500,
        "conditional_per_request_reservation_nano_usd": ProbePlan().reservation,
    }


def execute(
    run_id: str,
    registry: Path,
    approval: ProbeApproval | None,
    *,
    transport: RecordingProbeTransport | HTTPProbeTransport | None = None,
) -> JsonObject:
    if approval is None:
        raise PermissionError("explicit probe approval required")
    if type(transport) is not RecordingProbeTransport:
        raise PermissionError("v2 HTTP execution disabled; continuation authority required")
    wire = transport
    synthetic = True
    if wire.claimed:
        raise PermissionError("transport already used; fresh instance required")
    corpus = probe.prepare()
    bound = binding(run_id, registry, corpus)
    approval.validate(bound, synthetic=synthetic)
    # Admission is durable before any credential/transport invocation.
    journal = ProbeJournal(registry, bound, approval, corpus, synthetic=synthetic)
    run_admitted(journal, wire, corpus, bound, Ledger())
    return make_report(corpus, read_journal(registry, run_id))


def run_admitted(
    journal: ProbeJournal,
    wire: RecordingProbeTransport | HTTPProbeTransport,
    corpus: JsonObject,
    bound: JsonObject,
    ledger: Ledger,
) -> None:
    # HTTP never enters through the legacy journal, including an injected connection.
    if type(wire) is HTTPProbeTransport:
        from journeymap.adapters.probe_lineage import ContinuationJournal

        if type(journal) is not ContinuationJournal or not journal.permits_http:
            raise PermissionError("durable continuation claim required before HTTP")
    elif type(wire) is not RecordingProbeTransport:
        raise PermissionError("unreviewed transport")
    wire.claimed = True
    synthetic = type(wire) is RecordingProbeTransport
    run_id = str(bound["run_id"])
    seen = set[str]()
    stop = "ALL_SLOTS_ATTEMPTED"
    try:
        for request in rows(corpus["requests"]):
            journal.assert_current()
            blocked = ledger.blocked()
            if blocked is not None:
                stop = blocked
                break
            ledger.reserve()
            journal.append(
                "RESERVED",
                {
                    "request_id": request["request_id"],
                    "body_digest": request["body_digest"],
                    "ledger": ledger.data,
                },
            )
            try:
                wire.prepare()
            except Exception as exc:
                ledger.reserved_cost_nano_usd = 0
                stop = "CREDENTIAL_UNAVAILABLE"
                journal.append(
                    "NOT_SENT",
                    {
                        "reason": stop,
                        "diagnostic": exception_diagnostic("CREDENTIAL", exc).linked(
                            run_id, request, "NOT_SENT", len(journal.entries) + 1
                        ),
                    },
                )
                break
            journal.append(
                "ATTEMPTED", {"request_id": request["request_id"], "body": request["body"]}
            )
            try:
                reply = wire.send(clone_json_object(object_(request["body"])))
            except Exception as exc:
                reply = WireReply(
                    None, None, 0, "TRANSPORT_UNCERTAIN", exception_diagnostic("RUNNER", exc)
                )
            diagnostic = reply.diagnostic
            if diagnostic is None and synthetic:
                diagnostic = TransportDiagnostic("RECORDING", "NONE", "RECORDED")
            if diagnostic is None:
                raise ValueError("missing transport diagnostic; run remains open")
            packet: JsonObject = {
                "request_id": request["request_id"],
                "body_digest": request["body_digest"],
                "http_status": reply.http_status,
                "raw_response": reply.raw_response,
                "elapsed_ms": reply.elapsed_ms,
            }
            journal.append(
                "RESPONSE",
                {
                    "packet": packet,
                    "transport_error": reply.error,
                    "diagnostic": diagnostic.linked(
                        run_id, request, "RESPONSE", len(journal.entries) + 1
                    ),
                },
            )
            result = classify(packet, seen)
            if reply.error is not None:
                result["classification"] = reply.error
            ledger.settle(result)
            journal.append("SETTLED", {"result": result, "ledger": ledger.data})
            if result["classification"] not in CONTINUE:
                stop = str(result["classification"])
                break
        journal.append("RUN_STOPPED", {"reason": stop, "ledger": ledger.data})
    finally:
        journal.close()
        if type(wire) is HTTPProbeTransport:
            wire._key = None


def summarize(corpus: JsonObject, reduced: JsonObject) -> JsonObject:
    packets = rows(reduced["packets"])
    outcomes = rows(reduced["outcomes"])
    diagnostics = {str(d["request_id"]): d for d in rows(reduced["diagnostics"])}
    # Only READY packets enter the existing branch evaluator. Accounting and failure
    # denominators below are rebuilt from this protocol, never from imported estimates.
    ready = [
        p
        for p, outcome in zip(packets, outcomes, strict=True)
        if outcome["classification"] == "READY"
    ]
    evaluated = probe.evaluate(
        corpus,
        {
            "version": probe.VERSION,
            "corpus_digest": corpus["corpus_digest"],
            "evidence_kind": "imported-unverified",
            "records": cast(list[JsonValue], ready),
        },
    )
    engines = {str(row["request_id"]): row["engine"] for row in rows(evaluated["rows"])}
    received = {str(p["request_id"]): (p, o) for p, o in zip(packets, outcomes, strict=True)}
    results: list[JsonValue] = []
    attempts = cast(int, object_(reduced["ledger"])["attempts"])
    for index, request in enumerate(rows(corpus["requests"])):
        rid = str(request["request_id"])
        row: JsonObject = {
            "request_id": rid,
            "condition": request["condition"],
            "case_id": request["case_id"],
            "repeat": request["repeat"],
            "classification": "NOT_ATTEMPTED",
            "usage": None,
            "cost_nano_usd": 0,
            "elapsed_ms": None,
            "engine": None,
        }
        if rid in received:
            packet, outcome = received[rid]
            row.update(outcome)
            # Runner fallback stores 0 as an envelope sentinel, not measured latency.
            row["elapsed_ms"] = (
                None if diagnostics[rid]["stage"] == "RUNNER" else packet["elapsed_ms"]
            )
            row["engine"] = engines[rid]
        elif index < attempts:
            row["classification"] = (
                "NOT_SENT" if reduced["stop_reason"] == "CREDENTIAL_UNAVAILABLE" else "UNRESOLVED"
            )
            row["cost_nano_usd"] = 0 if row["classification"] == "NOT_SENT" else None
        results.append(row)
    summaries: JsonObject = {}
    for condition in probe.CONDITIONS:
        group = [row for row in rows(results) if row["condition"] == condition]
        completed = sum(
            row["engine"] is not None and object_(row["engine"])["status"] == "SUCCEEDED"
            for row in group
        )
        cost = sum(
            cast(int, row["cost_nano_usd"]) for row in group if row["cost_nano_usd"] is not None
        )
        cost_known = all(row["cost_nano_usd"] is not None for row in group)
        measured = [cast(int, row["elapsed_ms"]) for row in group if row["elapsed_ms"] is not None]
        summaries[condition] = {
            "planned": len(group),
            "classification_counts": dict(Counter(str(row["classification"]) for row in group)),
            "completed_actions": completed,
            "usage_reported_count": sum(row["usage"] is not None for row in group),
            "reported_token_totals": {
                key: sum(
                    cast(int, object_(row["usage"])[key])
                    for row in group
                    if row["usage"] is not None
                )
                for key in ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens")
            },
            "known_cost_nano_usd": cost,
            "total_cost_nano_usd": cost if cost_known else None,
            "cost_per_completed_action_nano_usd": cost / completed
            if completed and cost_known
            else None,
            "latency_known_count": len(measured),
            "mean_latency_ms": sum(measured) / len(measured) if measured else None,
            "behavioral_success_rate": None,
            "24h_completion_rate": None,
        }
    return {
        "rows": results,
        "conditions": summaries,
        "token_saving_effect_established": False,
        "deterministic_fixture_count": 12,
        "rate_card_is_invoice": False,
        "pending_slots_excluded_from_denominator": False,
    }


def make_report(corpus: JsonObject, journal: JsonObject) -> JsonObject:
    bound = object_(journal["binding"])
    if not same(corpus, probe.prepare()):
        raise ValueError("corpus/source mismatch")
    # Registry path is recorded provenance during portable data-only replay.
    expected = binding(str(bound["run_id"]), Path("probe-runs.sqlite3"), corpus)
    expected["registry"] = bound["registry"]
    if not same(bound, expected) or set(journal) != {
        "binding",
        "approval_id",
        "entries",
        "snapshot",
    }:
        raise ValueError("journal binding mismatch")
    reduced = reduce_trace(bound, corpus, rows(journal["entries"]))
    if (
        not same(journal["snapshot"], reduced["snapshot"])
        or object_(reduced["approval"])["approval_id"] != journal["approval_id"]
    ):
        raise ValueError("snapshot/approval mismatch")
    return {
        "protocol": PROTOCOL,
        "corpus": corpus,
        "journal": journal,
        "accounting": {
            k: v
            for k, v in reduced.items()
            if k not in {"packets", "outcomes", "approval", "diagnostics"}
        },
        "transport_diagnostics": reduced["diagnostics"],
        "evaluation": summarize(corpus, reduced),
        "audit_scope": (
            "local journal consistency and engine replay; not provider authenticity or invoice"
        ),
        "live_authorization_independently_verified": False,
    }


def audit_report(report: JsonObject) -> JsonObject:
    rebuilt = make_report(object_(report["corpus"]), object_(report["journal"]))
    if not same(report, rebuilt):
        raise ValueError("report differs from journal-derived replay")
    return {
        "data_only_equal": True,
        "provider_recalled": False,
        "status": object_(rebuilt["accounting"])["status"],
        "live_authorization_independently_verified": False,
    }
