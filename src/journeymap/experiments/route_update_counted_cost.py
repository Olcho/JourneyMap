"""E04 count and generation reservations; no generic pricing framework or count reuse."""

from decimal import Decimal
from hashlib import sha256
from typing import cast

from journeymap.adapters.route_update import obj
from journeymap.adapters.route_update_count import token_count
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.experiments.pilot_journal import digest
from journeymap.experiments.route_update_live_cost import RESERVATION, CostStop, Ledger


class CountedLedger(Ledger):
    def __init__(
        self,
        cap: str,
        generation_cap: int,
        count_cap: int,
        network_cap: int,
        count_fee_bound: str | None,
        *,
        cost_policy: str | None = None,
    ) -> None:
        # Count-only has a zero generation cap; base ledger requires a positive cap.
        super().__init__(cap, max(generation_cap, 1))
        self.call_cap = generation_cap
        from journeymap.experiments.route_update_limited_pilot import POLICY as PILOT_POLICY
        from journeymap.experiments.route_update_limited_smoke import POLICY

        self.cost_policy = cost_policy
        self.unknown_count_fee = cost_policy in (POLICY, PILOT_POLICY)
        if cost_policy is not None and not self.unknown_count_fee:
            raise ValueError("E04 unsupported count cost policy")
        self.count_fee = Decimal(count_fee_bound) if count_fee_bound is not None else None
        self.count_cap, self.network_cap = count_cap, network_cap
        if self.unknown_count_fee:
            self._limited_scope()
        elif self.count_fee is None:
            raise CostStop("COUNT_PRICE_UNKNOWN")
        if self.count_fee is not None and (not self.count_fee.is_finite() or self.count_fee < 0):
            raise ValueError("E04 reviewed count fee bound required")
        self.count_cap, self.network_cap = count_cap, network_cap
        self.counts: list[JsonObject] = []

    def _limited_scope(self) -> None:
        from journeymap.experiments.route_update_limited_pilot import POLICY as PILOT_POLICY

        pilot = self.cost_policy == PILOT_POLICY
        expected_caps = (114, 114, 228) if pilot else (1, 1, 2)
        if (
            self.count_fee is not None
            or self.cap != RESERVATION * (114 if pilot else 1)
            or any(type(c) is not int for c in (self.call_cap, self.count_cap, self.network_cap))
            or (self.call_cap, self.count_cap, self.network_cap) != expected_caps
        ):
            raise ValueError("E04 exact policy-specific generation reservation and caps required")

    def _budget(self, next_amount: Decimal | None) -> None:
        if self.unknown_count_fee:
            self._limited_scope()
            # This is a generation-only comparison. None stays unknown, never a count price.
            known_generation_reserved = RESERVATION * len(self.entries)
            if next_amount is not None:
                known_generation_reserved += next_amount
            if known_generation_reserved > self.cap:
                raise CostStop("BUDGET_LIMIT_NOT_SENT")
            return
        assert self.count_fee is not None and next_amount is not None
        if (
            RESERVATION * len(self.entries) + self.count_fee * len(self.counts) + next_amount
            > self.cap
        ):
            raise CostStop("BUDGET_LIMIT_NOT_SENT")

    def begin_count(self, counted_wire: JsonObject, mode: str) -> JsonObject:
        if self.unknown_count_fee:
            self._limited_scope()
        if mode not in ("live", "recording-mock"):
            raise ValueError("E04 count mode")
        if (
            len(self.counts) >= self.count_cap
            or len(self.counts) + len(self.entries) >= self.network_cap
        ):
            raise CostStop("COUNT_CALL_LIMIT_NOT_SENT")
        self._budget(self.count_fee)
        entry: JsonObject = {
            "wire": counted_wire,
            "mode": mode,
            "count_reservation_usd": None if self.unknown_count_fee else str(self.count_fee),
            "sent": None,
            "raw_utf8": None,
            "failure": None,
            "input_tokens": None,
            "validation": "ABSENT",
            "generation_reserved": False,
        }
        if self.unknown_count_fee:
            entry.update(
                {
                    "count_price_status": "UNKNOWN",
                    "count_fee_upper_bound_usd": None,
                    "count_charge_estimate_usd": None,
                }
            )
        self.counts.append(entry)
        return dict(entry)

    def receive_count(self, raw: str | None, failure: JsonObject | None) -> str | None:
        entry = self.counts[-1]
        entry.update({"raw_utf8": raw, "failure": failure})
        if raw is None or failure is not None:
            entry["validation"] = "COUNT_INFRASTRUCTURE"
        else:
            try:
                count = token_count(raw)
            except (ValueError, TypeError):
                entry["validation"] = "COUNT_INVALID_RESPONSE"
            else:
                entry["input_tokens"] = count
                entry["validation"] = (
                    "VALID" if count <= 20000 else "COUNT_INPUT_LIMIT_NOT_GENERATED"
                )
        return None if entry["validation"] == "VALID" else cast(str, entry["validation"])

    def reserve(self, wire: JsonObject, mode: str, *, mock_input_tokens: int = 1000) -> JsonObject:
        if self.unknown_count_fee:
            self._limited_scope()
        if not self.counts:
            raise CostStop("COUNT_EVIDENCE_MISSING")
        count = self.counts[-1]
        text = wire.get("canonical_utf8")
        if (
            type(text) is not str
            or canonical_json(obj(wire.get("body"))) != text
            or sha256(text.encode()).hexdigest() != wire.get("sha256")
            or len(text.encode()) != wire.get("bytes")
        ):
            raise CostStop("COUNT_GENERATION_BINDING_ERROR")
        if (
            count["mode"] != mode
            or count["validation"] != "VALID"
            or count["generation_reserved"] is not False
            or obj(count["wire"])["generation_wire_sha256"] != wire["sha256"]
        ):
            raise CostStop("COUNT_GENERATION_BINDING_ERROR")
        if (
            len(self.entries) >= self.call_cap
            or len(self.entries) + len(self.counts) >= self.network_cap
        ):
            raise CostStop("CALL_LIMIT_NOT_SENT")
        self._budget(RESERVATION)
        entry: JsonObject = {
            "wire_sha256": wire["sha256"],
            "reservation_usd": str(RESERVATION),
            "counter": "experiment04-responses-input-count-1",
            "input_bound": 20000,
            "count_evidence_sha256": digest(count),
            "count_number": len(self.counts),
            "counted_input_tokens": count["input_tokens"],
            "sent": None,
            "confirmed_estimate_usd": None,
            "usage": None,
        }
        self.entries.append(entry)
        count["generation_reserved"] = True
        return dict(entry)

    def settle(self, document: JsonObject) -> None:
        super().settle(document)
        input_tokens = obj(document["usage"])["input_tokens"]
        if input_tokens != self.entries[-1]["counted_input_tokens"]:
            self.entries[-1]["usage_count_mismatch"] = {
                "counted": self.entries[-1]["counted_input_tokens"],
                "returned": input_tokens,
            }
            raise CostStop("COUNT_USAGE_MISMATCH")

    def snapshot(self) -> JsonObject:
        value = super().snapshot()
        if self.unknown_count_fee:
            from journeymap.experiments.route_update_limited_pilot import COST as PILOT_COST
            from journeymap.experiments.route_update_limited_pilot import POLICY as PILOT_POLICY
            from journeymap.experiments.route_update_limited_smoke import COST

            self._limited_scope()
            cost = PILOT_COST if self.cost_policy == PILOT_POLICY else COST
            value.update(
                {
                    **cost,
                    "generation_reserved_usd": value.pop("reserved_usd"),
                    "generation_confirmed_usage_estimate_usd": value.pop(
                        "confirmed_usage_estimate_usd"
                    ),
                    "generation_unconfirmed_reservation_usd": value.pop(
                        "unconfirmed_reservation_usd"
                    ),
                    "generation_budget_cap_usd": value.pop("budget_cap_usd"),
                    "count_charge_estimate_usd": None,
                    "count_entries": cast(list[JsonValue], self.counts.copy()),
                    "count_cap": self.count_cap,
                    "network_cap": self.network_cap,
                    "count_intents": len(self.counts),
                    "count_send_attempts": sum(e["sent"] is not None for e in self.counts),
                    "count_charge": (
                        "UNKNOWN; attempts preserved including transport/recording failures"
                    ),
                    "cost_label": (
                        "generation subtotal only, usage-derived estimate; "
                        "total unknown, not invoice"
                    ),
                }
            )
            return value
        assert self.count_fee is not None
        value.update(
            {
                "count_entries": cast(list[JsonValue], self.counts.copy()),
                "count_cap": self.count_cap,
                "network_cap": self.network_cap,
                "count_reserved_usd": str(self.count_fee * len(self.counts)),
                "combined_reserved_usd": str(
                    RESERVATION * len(self.entries) + self.count_fee * len(self.counts)
                ),
                "count_charge": (
                    "REVIEWED_BOUND_ONLY; no usage fee or invoice supplied by count response"
                ),
                "count_unknown_reservation_usd": str(
                    self.count_fee * sum(c["validation"] != "VALID" for c in self.counts)
                ),
            }
        )
        return value


def ledger_for_plan(plan: JsonObject) -> CountedLedger:
    from journeymap.experiments.route_update_limited_pilot import POLICY as PILOT_POLICY

    if plan.get("cost_policy") == PILOT_POLICY:
        from journeymap.experiments.route_update_limited_pilot import validate_scope as pilot_scope

        pilot_scope(plan)
        return CountedLedger("8.03472", 114, 114, 228, None, cost_policy=PILOT_POLICY)
    if "cost_policy" in plan:
        from journeymap.experiments.route_update_limited_smoke import POLICY, validate_scope

        validate_scope(plan)
        return CountedLedger("0.07048", 1, 1, 2, None, cost_policy=POLICY)
    review = obj(plan["count_price_review"])
    fee = (
        review.get("per_count_max_usd")
        if plan["mode"] == "recording-mock"
        else review.get("upper_bound_usd")
    )
    return CountedLedger(
        cast(str, obj(plan["budget_review"])["stage_reservation_cap_usd"]),
        cast(int, plan["generation_cap"]),
        cast(int, plan["count_cap"]),
        cast(int, plan["network_cap"]),
        cast(str | None, fee),
    )
