"""Allowlisted transport observations, never exception text or accounting authority."""

import http.client
import socket
import ssl
from dataclasses import dataclass
from typing import cast

from journeymap.core.canonical import JsonObject, JsonValue

DIAGNOSTIC_VERSION = "probe-transport-diagnostic-1"
STAGES = {
    "CONNECTION_OBJECT",
    "REQUEST",
    "RESPONSE_HEADERS",
    "BODY_READ",
    "FRAMING_CHECK",
    "DECODE_JSON",
    "SECRET_SCAN",
    "REQUEST_SCAN",
    "RUNNER",
    "CREDENTIAL",
    "RECORDING",
}
CATEGORIES = {
    "NONE",
    "TIMEOUT",
    "DNS",
    "TLS",
    "REMOTE_DISCONNECTED",
    "CONNECTION_RESET",
    "CONNECTION_REFUSED",
    "INCOMPLETE_READ",
    "OS_ERROR",
    "UNICODE_ERROR",
    "VALUE_ERROR",
    "RECURSION_ERROR",
    "OTHER",
}
DISPOSITIONS = {
    "COMPLETE",
    "LATE_COMPLETE",
    "EXCEPTION",
    "DEADLINE_PARTIAL",
    "FRAMING_INCOMPLETE",
    "SIZE_LIMIT",
    "INVALID_RESPONSE",
    "SECRET_ECHO",
    "RECORDED",
}
TRANSPORT_ERRORS = {
    "TRANSPORT_UNCERTAIN",
    "TIMEOUT",
    "SECRET_ECHO",
    "INVALID_RESPONSE",
    "RESPONSE_TOO_LARGE",
}


def exception_category(exc: Exception) -> str:
    # Specific subclasses precede broad OSError/ValueError. Never inspect args/text/names.
    for cls, category in (
        (TimeoutError, "TIMEOUT"),
        (socket.gaierror, "DNS"),
        (ssl.SSLError, "TLS"),
        (http.client.RemoteDisconnected, "REMOTE_DISCONNECTED"),
        (ConnectionResetError, "CONNECTION_RESET"),
        (ConnectionRefusedError, "CONNECTION_REFUSED"),
        (http.client.IncompleteRead, "INCOMPLETE_READ"),
        (OSError, "OS_ERROR"),
        (UnicodeError, "UNICODE_ERROR"),
        (ValueError, "VALUE_ERROR"),
        (RecursionError, "RECURSION_ERROR"),
    ):
        if isinstance(exc, cls):
            return category
    return "OTHER"


@dataclass(frozen=True, slots=True)
class TransportDiagnostic:
    stage: str
    category: str
    disposition: str
    framing_complete: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.stage) is not str
            or self.stage not in STAGES
            or type(self.category) is not str
            or self.category not in CATEGORIES
            or type(self.disposition) is not str
            or self.disposition not in DISPOSITIONS
            or type(self.framing_complete) is not bool
        ):
            raise ValueError("invalid transport diagnostic")

    def linked(self, run_id: str, request: JsonObject, event: str, ordinal: int) -> JsonObject:
        return {
            "version": DIAGNOSTIC_VERSION,
            "run_id": run_id,
            "request_id": request["request_id"],
            "body_digest": request["body_digest"],
            "journal_stage": event,
            "journal_ordinal": ordinal,
            "stage": self.stage,
            "category": self.category,
            "disposition": self.disposition,
            "framing_complete": self.framing_complete,
        }


def exception_diagnostic(stage: str, exc: Exception) -> TransportDiagnostic:
    return TransportDiagnostic(
        stage, exception_category(exc), "EXCEPTION", stage in {"DECODE_JSON", "SECRET_SCAN"}
    )


def exception_error(diagnostic: TransportDiagnostic) -> str:
    return (
        "INVALID_RESPONSE"
        if diagnostic.stage == "DECODE_JSON"
        and diagnostic.category in {"UNICODE_ERROR", "VALUE_ERROR", "RECURSION_ERROR"}
        else "TRANSPORT_UNCERTAIN"
    )


def validate_diagnostic(
    value: JsonValue,
    *,
    run_id: str,
    request: JsonObject,
    event: str,
    ordinal: int,
    error: JsonValue,
    status: JsonValue = None,
    raw: JsonValue = None,
    elapsed: JsonValue = 0,
    synthetic: bool = False,
) -> None:
    """Structural and journal/packet semantics, not independent proof of a wire event."""
    fields = {
        "version",
        "run_id",
        "request_id",
        "body_digest",
        "journal_stage",
        "journal_ordinal",
        "stage",
        "category",
        "disposition",
        "framing_complete",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("missing or invalid diagnostic fields")
    expected: JsonObject = {
        "version": DIAGNOSTIC_VERSION,
        "run_id": run_id,
        "request_id": request["request_id"],
        "body_digest": request["body_digest"],
        "journal_stage": event,
        "journal_ordinal": ordinal,
    }
    if any(type(value[k]) is not type(v) or value[k] != v for k, v in expected.items()):
        raise ValueError("diagnostic journal binding mismatch")
    d = TransportDiagnostic(
        cast(str, value["stage"]),
        cast(str, value["category"]),
        cast(str, value["disposition"]),
        cast(bool, value["framing_complete"]),
    )
    valid = False
    if event == "NOT_SENT":
        valid = (
            d.stage == "CREDENTIAL"
            and d.disposition == "EXCEPTION"
            and d.category != "NONE"
            and not d.framing_complete
            and error == "CREDENTIAL_UNAVAILABLE"
            and status is None
            and raw is None
        )
    elif event == "RESPONSE":
        if d.disposition == "RECORDED":
            valid = (
                synthetic
                and d.stage == "RECORDING"
                and d.category == "NONE"
                and not d.framing_complete
            )
        elif d.disposition == "EXCEPTION":
            valid = (
                d.stage not in {"CREDENTIAL", "RECORDING"}
                and d.category != "NONE"
                and error == exception_error(d)
                and raw is None
                and d.framing_complete == (d.stage in {"DECODE_JSON", "SECRET_SCAN"})
            )
            if d.stage == "RUNNER":
                valid &= status is None and elapsed == 0
        elif d.category == "NONE":
            if d.disposition in {"COMPLETE", "LATE_COMPLETE"}:
                valid = (
                    d.stage == "SECRET_SCAN"
                    and d.framing_complete
                    and error is None
                    and type(raw) is str
                    and type(elapsed) is int
                    and (elapsed > 120_000) == (d.disposition == "LATE_COMPLETE")
                )
            elif d.disposition == "SECRET_ECHO":
                valid = (
                    d.stage in {"REQUEST_SCAN", "SECRET_SCAN"}
                    and raw is None
                    and error == "SECRET_ECHO"
                    and d.framing_complete == (d.stage == "SECRET_SCAN")
                )
            elif d.disposition == "INVALID_RESPONSE":
                valid = (
                    d.stage == "DECODE_JSON"
                    and d.framing_complete
                    and raw is None
                    and error == "INVALID_RESPONSE"
                )
            elif d.disposition == "FRAMING_INCOMPLETE":
                valid = (
                    d.stage == "FRAMING_CHECK"
                    and not d.framing_complete
                    and raw is None
                    and error == "TRANSPORT_UNCERTAIN"
                )
            elif d.disposition == "SIZE_LIMIT":
                valid = (
                    d.stage == "BODY_READ"
                    and not d.framing_complete
                    and raw is None
                    and error == "RESPONSE_TOO_LARGE"
                )
            elif d.disposition == "DEADLINE_PARTIAL":
                valid = (
                    d.stage == "BODY_READ"
                    and not d.framing_complete
                    and raw is None
                    and error == "TIMEOUT"
                    and type(elapsed) is int
                    and elapsed > 120_000
                )
        if d.stage in {
            "REQUEST_SCAN",
            "CONNECTION_OBJECT",
            "REQUEST",
            "RESPONSE_HEADERS",
            "RUNNER",
        }:
            valid &= status is None
        elif d.stage != "RECORDING":
            valid &= type(status) is int and 100 <= status <= 599
    if not valid:
        raise ValueError("contradictory transport diagnostic")
