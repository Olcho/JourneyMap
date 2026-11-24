"""One-followup candidate contract. Proposals and budget arithmetic grant no authority."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import cast

from journeymap.adapters.life_admission import source_identity
from journeymap.adapters.probe_diagnostics import DIAGNOSTIC_VERSION
from journeymap.adapters.probe_execution import (
    ENDPOINT,
    Ledger,
    ProbeApproval,
    ProbePlan,
    object_,
    rows,
    same,
    uuid_,
)
from journeymap.core.canonical import JsonObject, JsonValue, state_digest

PROTOCOL = "life-reasoning-probe-execution-3"
POLICY = "probe-uncertain-one-followup-1"
AUTHORITY_VERSION = "probe-lineage-authority-1"
APPROVAL_SCHEMA = "probe-continuation-approval-1"
PRIOR_KNOWN = 120_455_850
PRIOR_UNRESOLVED = 336_864_000


@dataclass(frozen=True, slots=True)
class FollowupPlan(ProbePlan):
    cost_limit_nano_usd: int = 24_260_000_000
    total_input_tokens: int = 28_800_000
    plan_version: str = "probe-followup-max72-1"
    lineage_cost_limit_nano_usd: int = 24_717_319_850


@dataclass(slots=True)
class FollowupLedger(Ledger):
    prior_known_cost_nano_usd: int = PRIOR_KNOWN
    prior_unresolved_nano_usd: int = PRIOR_UNRESOLVED

    def blocked(self) -> str | None:
        p = FollowupPlan()
        if self.reserved_cost_nano_usd:
            return "UNRESOLVED_RESERVATION"
        if self.attempts >= p.request_limit:
            return "REQUEST_LIMIT"
        if self.input_tokens + p.context_input_reserve > p.total_input_tokens:
            return "INPUT_TOKEN_LIMIT"
        if self.output_tokens + p.max_output_tokens > p.total_output_tokens:
            return "OUTPUT_TOKEN_LIMIT"
        if self.known_cost_nano_usd + p.reservation > p.cost_limit_nano_usd:
            return "COST_LIMIT"
        if (
            self.prior_known_cost_nano_usd
            + self.prior_unresolved_nano_usd
            + self.known_cost_nano_usd
            + p.reservation
            > p.lineage_cost_limit_nano_usd
        ):
            return "LINEAGE_COST_LIMIT"
        return None


def exposure(ledger: JsonObject) -> JsonObject:
    known = cast(int, ledger["known_cost_nano_usd"])
    reserved = cast(int, ledger["reserved_cost_nano_usd"])
    total = PRIOR_KNOWN + PRIOR_UNRESOLVED + known + reserved
    if any(type(v) is not int or v < 0 for v in (known, reserved)):
        raise ValueError("invalid lineage accounting")
    if total > FollowupPlan().lineage_cost_limit_nano_usd:
        raise ValueError("lineage exposure exceeds approved candidate")
    return {
        "prior_known_cost_nano_usd": PRIOR_KNOWN,
        "prior_unresolved_nano_usd": PRIOR_UNRESOLVED,
        "new_known_cost_nano_usd": known,
        "new_reserved_nano_usd": reserved,
        "known_cost_sum_nano_usd": PRIOR_KNOWN + known,
        "unresolved_exposure_sum_nano_usd": PRIOR_UNRESOLVED + reserved,
        "conditional_exposure_nano_usd": total,
        "lineage_total_cost_nano_usd": None,
        "approved_capacity_is_observed_cost": False,
    }


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def timestamp(value: str) -> datetime:
    if type(value) is not str:
        raise PermissionError("invalid approval time")
    try:
        d = datetime.fromisoformat(value)
        offset = d.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError
        return d
    except (ValueError, TypeError):
        raise PermissionError("invalid approval time") from None


@dataclass(frozen=True, slots=True)
class Anchor:
    authority_id: str
    path: str
    device: str
    history_digest: str
    sequence: int
    tail: str

    @property
    def data(self) -> JsonObject:
        return cast(JsonObject, asdict(self))

    @classmethod
    def parse(cls, value: JsonValue) -> "Anchor":
        d = object_(value)
        if set(d) != {"authority_id", "path", "device", "history_digest", "sequence", "tail"}:
            raise ValueError("invalid authority anchor")
        if any(
            type(d[k]) is not str or not d[k]
            for k in ("authority_id", "path", "device", "history_digest", "tail")
        ):
            raise ValueError("invalid authority anchor")
        if type(d["sequence"]) is not int or d["sequence"] < 1:
            raise ValueError("invalid authority anchor sequence")
        uuid_(str(d["authority_id"]))
        if not (
            Path(str(d["path"])).is_absolute() or PureWindowsPath(str(d["path"])).is_absolute()
        ):
            raise ValueError("authority path must be absolute")
        return cls(
            str(d["authority_id"]),
            str(d["path"]),
            str(d["device"]),
            str(d["history_digest"]),
            d["sequence"],
            str(d["tail"]),
        )


def candidate_binding(
    run_id: str,
    approval_id: str,
    risk_id: str,
    corpus: JsonObject,
    anchor: Anchor,
    history: JsonObject,
) -> JsonObject:
    for value in (run_id, approval_id, risk_id):
        uuid_(value)
    if len({run_id, approval_id, risk_id}) != 3:
        raise PermissionError("continuation IDs must be distinct")
    if corpus["source_identity"] != source_identity():
        raise PermissionError("continuation source mismatch")
    if len(rows(corpus["requests"])) != 72 or state_digest(history) != anchor.history_digest:
        raise PermissionError("continuation history or schedule mismatch")
    return {
        "policy_version": POLICY,
        "protocol": PROTOCOL,
        "transport_diagnostic_version": DIAGNOSTIC_VERSION,
        "run_id": run_id,
        "paid_approval_id": approval_id,
        "risk_acceptance_id": risk_id,
        "source_digest": source_identity(),
        "corpus_digest": corpus["corpus_digest"],
        "schedule_digest": state_digest({"requests": corpus["requests"]}),
        "plan": FollowupPlan().data,
        "endpoint": ENDPOINT,
        "authority_checkpoint": anchor.data,
        "prior_uncertain_target": history["uncertain_target"],
        "prior_known_cost_nano_usd": PRIOR_KNOWN,
        "prior_unresolved_nano_usd": PRIOR_UNRESOLVED,
        "prior_total_cost_nano_usd": None,
        "one_successor_only": True,
        "allocation_policy": (
            "frozen-corpus-order; independent branches; no retries or replacements"
        ),
        "failure_policy": "continue-ready-incomplete-refusal; otherwise-stop",
    }


@dataclass(frozen=True, slots=True)
class ContinuationApproval:
    approval_id: str
    risk_acceptance_id: str
    approved_by: str
    decision: str
    scope: tuple[str, ...]
    valid_from: str
    expires_at: str
    bound: JsonObject

    @property
    def data(self) -> JsonObject:
        return {
            "schema": APPROVAL_SCHEMA,
            "approval_id": self.approval_id,
            "risk_acceptance_id": self.risk_acceptance_id,
            "approved_by": self.approved_by,
            "decision": self.decision,
            "scope": list(self.scope),
            "valid_from": self.valid_from,
            "expires_at": self.expires_at,
            "bound": self.bound,
        }

    @classmethod
    def parse(cls, value: JsonValue) -> "ContinuationApproval":
        d = object_(value)
        if (
            set(d)
            != {
                "schema",
                "approval_id",
                "risk_acceptance_id",
                "approved_by",
                "decision",
                "scope",
                "valid_from",
                "expires_at",
                "bound",
            }
            or d["schema"] != APPROVAL_SCHEMA
        ):
            raise PermissionError("explicit continuation approval required")
        if any(
            type(d[k]) is not str
            for k in (
                "approval_id",
                "risk_acceptance_id",
                "approved_by",
                "decision",
                "valid_from",
                "expires_at",
            )
        ):
            raise PermissionError("invalid continuation approval")
        scope = d["scope"]
        if not isinstance(scope, list) or any(type(x) is not str for x in scope):
            raise PermissionError("invalid continuation scope")
        return cls(
            str(d["approval_id"]),
            str(d["risk_acceptance_id"]),
            str(d["approved_by"]),
            str(d["decision"]),
            tuple(cast(list[str], scope)),
            str(d["valid_from"]),
            str(d["expires_at"]),
            object_(d["bound"]),
        )

    def validate(self, expected: JsonObject, *, synthetic: bool, now: str) -> None:
        for value in (self.approval_id, self.risk_acceptance_id):
            uuid_(value)
        valid_actor = "offline-fixture" if synthetic else "human-user"
        decision = "synthetic-continuation-only" if synthetic else "approve-one-probe-continuation"
        scope = (
            ("recording-only", "synthetic-risk-only")
            if synthetic
            else ("accept-one-prior-uncertainty", "read-openai-api-key", "paid-responses")
        )
        if (
            self.approved_by != valid_actor
            or self.decision != decision
            or self.scope != scope
            or self.approval_id != expected["paid_approval_id"]
            or self.risk_acceptance_id != expected["risk_acceptance_id"]
            or not same(self.bound, expected)
        ):
            raise PermissionError("mismatched continuation approval")
        start, end, claimed = timestamp(self.valid_from), timestamp(self.expires_at), timestamp(now)
        if not start <= claimed < end:
            raise PermissionError("continuation approval outside validity window")

    def payment_projection(self, *, synthetic: bool) -> ProbeApproval:
        return ProbeApproval(
            self.approval_id,
            self.approved_by,
            "synthetic-probe-only" if synthetic else "approve-one-paid-probe",
            ("recording-only",) if synthetic else ("read-openai-api-key", "paid-responses"),
            self.bound,
        )


def inactive_proposal() -> JsonObject:
    return {
        "schema": "probe-continuation-policy-proposal-1",
        "authorized": False,
        "policy_version": POLICY,
        "protocol": PROTOCOL,
        "plan": FollowupPlan().data,
        "prior_known_cost_nano_usd": PRIOR_KNOWN,
        "prior_unresolved_nano_usd": PRIOR_UNRESOLVED,
        "prior_total_cost_nano_usd": None,
        "unissued": [
            "run_id",
            "paid_approval_id",
            "risk_acceptance_id",
            "authority_identity",
            "trusted_latest_checkpoint",
        ],
        "operational_history_imported": False,
    }
