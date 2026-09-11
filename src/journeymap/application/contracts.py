"""Small explicit 0.1 actor contract; domain parsers remain module-owned."""

from collections.abc import Callable
from dataclasses import asdict
from functools import partial
from types import MappingProxyType
from typing import cast

from journeymap.core.actions import validate_wait_payload
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.core.controller import GameSubmissionError
from journeymap.core.handlers import ActionRequest, ActionValidationError
from journeymap.modules.movement.handlers import validate_move_payload
from journeymap.modules.social.contracts import validate_payload
from journeymap.modules.survival.handlers import validate_consume_payload, validate_rest_payload
from journeymap.modules.trade.handlers import validate_buy_payload

ACTION_V1_VALIDATORS: MappingProxyType[tuple[str, int], Callable[[JsonObject], object]] = (
    MappingProxyType(
        {
            ("MOVE", 1): validate_move_payload,
            ("WAIT", 1): validate_wait_payload,
            ("ASK", 1): partial(validate_payload, "ASK"),
            ("INFORM", 1): partial(validate_payload, "INFORM"),
            ("REQUEST", 1): partial(validate_payload, "REQUEST"),
            ("REST", 1): validate_rest_payload,
            ("CONSUME", 1): validate_consume_payload,
            ("BUY", 1): validate_buy_payload,
        }
    )
)


def normalize_request(value: object) -> ActionRequest:
    """Revalidate the exact live envelope, including mutable payload and UTF-8."""
    try:
        if type(value) is not ActionRequest:
            raise ValueError("expected ActionRequest")
        identities = (
            value.action_request_id,
            value.run_id,
            value.actor_id,
            value.based_on_observation_id,
            value.action_type,
        )
        if any(type(item) is not str or not item for item in identities):
            raise ValueError("invalid identity")
        if value.correlation_id is not None and (
            type(value.correlation_id) is not str or not value.correlation_id
        ):
            raise ValueError("invalid correlation")
        if type(value.payload) is not dict:
            raise ValueError("payload must be an object")
        if type(value.submitted_at) is not int or type(value.schema_version) is not int:
            raise ValueError("invalid integer")
        detached = value.detached()
        request_fingerprint(detached).encode("utf-8")
        return detached
    except (TypeError, ValueError, RecursionError, AttributeError):
        raise GameSubmissionError("INVALID_REQUEST") from None


def request_fingerprint(request: ActionRequest) -> str:
    """Every envelope field participates; JSON object key order does not."""
    return canonical_json(cast(JsonObject, asdict(request)))


def validate_action_contract(request: ActionRequest) -> None:
    """Turn preflight only; live domain conditions still belong to handlers."""
    parser = ACTION_V1_VALIDATORS.get((request.action_type, request.schema_version))
    if parser is None:
        raise ActionValidationError("UNKNOWN_ACTION")
    parser(request.payload)
