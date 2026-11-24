"""Decimal reservations and honest token review. Never use byte/character heuristics."""

from decimal import Decimal
from typing import cast

from journeymap.adapters.route_update import obj
from journeymap.core.canonical import JsonObject, JsonValue

COUNTER_REVIEW: JsonObject = {
    "status": "INCOMPLETE",
    "method": None,
    "tokenizer_version": None,
    "model_mapping_verified": False,
    "schema_and_service_overhead_bound_verified": False,
    "input_cap_tokens": 20000,
    "measured_service_input_tokens": None,
    "reason": "No verified gpt-6.1-sol offline tokenizer and schema/service overhead bound. "
    "Bytes are measured only; the token-counting API is prohibited in this task.",
}
RATES: JsonObject = {
    "currency": "USD",
    "per_tokens": 1000000,
    "input": "2.00",
    "cached_input": "0.10",
    "cache_write": "2.50",
    "output": "10.00",
    "reservation_input": "2.50",
    "tier": "Standard",
    "input_scope": "<=272000",
}
RESERVATION = Decimal("0.07048")


class CostStop(ValueError):
    pass


class Ledger:
    """Keep every maximum reservation to terminal; unknown charges are never released."""

    def __init__(self, cap: str, call_cap: int) -> None:
        self.cap = Decimal(cap)
        if not self.cap.is_finite() or self.cap <= 0 or type(call_cap) is not int or call_cap <= 0:
            raise ValueError("positive finite budget and call cap required")
        self.call_cap = call_cap
        self.entries: list[JsonObject] = []

    def reserve(self, wire: JsonObject, mode: str, *, mock_input_tokens: int = 1000) -> JsonObject:
        # This counter is explicitly a synthetic transport contract, never live evidence.
        if mode != "recording-mock":
            raise CostStop("TOKEN_COUNTER_UNVERIFIED")
        if type(mock_input_tokens) is not int or not 0 <= mock_input_tokens <= 20000:
            raise CostStop("INPUT_LIMIT_NOT_SENT")
        if len(self.entries) >= self.call_cap:
            raise CostStop("CALL_LIMIT_NOT_SENT")
        if RESERVATION * (len(self.entries) + 1) > self.cap:
            raise CostStop("BUDGET_LIMIT_NOT_SENT")
        entry: JsonObject = {
            "wire_sha256": wire["sha256"],
            "reservation_usd": str(RESERVATION),
            "counter": "synthetic-mock-only-1",
            "input_bound": 20000,
            "mock_input_tokens": mock_input_tokens,
            "sent": None,
            "confirmed_estimate_usd": None,
            "usage": None,
        }
        self.entries.append(entry)
        return dict(entry)

    def settle(self, document: JsonObject) -> None:
        usage = obj(document.get("usage"))
        if document.get("service_tier") not in (None, "default"):
            raise CostStop("SERVICE_PRICING_UNSUPPORTED")
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            if type(usage.get(field)) is not int or cast(int, usage[field]) < 0:
                raise CostStop("USAGE_INVALID")
        inputs, outputs = cast(int, usage["input_tokens"]), cast(int, usage["output_tokens"])
        if inputs > 20000 or outputs > 2048 or usage["total_tokens"] != inputs + outputs:
            raise CostStop("USAGE_OUT_OF_BOUND")
        # All input at the highest reviewed input rate: usage-derived conservative estimate.
        cost = (Decimal(inputs) * Decimal("2.50") + Decimal(outputs) * 10) / 1000000
        self.entries[-1].update({"usage": usage, "confirmed_estimate_usd": str(cost)})

    def snapshot(self) -> JsonObject:
        return {
            "budget_cap_usd": str(self.cap),
            "call_cap": self.call_cap,
            "reservation_policy": "KEEP_ALL_MAXIMUM_RESERVATIONS",
            "entries": cast(list[JsonValue], self.entries.copy()),
            "reserved_usd": str(RESERVATION * len(self.entries)),
            "confirmed_usage_estimate_usd": str(
                sum(
                    (
                        Decimal(cast(str, e["confirmed_estimate_usd"]))
                        for e in self.entries
                        if e["confirmed_estimate_usd"] is not None
                    ),
                    Decimal(0),
                )
            ),
            "unconfirmed_reservation_usd": str(
                RESERVATION * sum(e["confirmed_estimate_usd"] is None for e in self.entries)
            ),
            "cost_label": "conservative usage-based estimated charge; not invoice",
        }
