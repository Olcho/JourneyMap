"""Canonical JSON state helpers used for deterministic comparison."""

import hashlib
import json
import math
from collections.abc import Mapping
from typing import cast

type JsonScalar = bool | int | float | str | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class CanonicalValueError(ValueError):
    """Raised when a value cannot be represented as canonical JSON."""


def canonical_json(value: JsonValue) -> str:
    """Serialize JSON data with stable keys and no non-finite numbers."""

    _validate_json_value(value, path="$")
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise CanonicalValueError(str(error)) from error


def _validate_json_value(value: object, *, path: str) -> None:
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise CanonicalValueError(f"non-finite float at {path}")
        return
    if type(value) is list:
        for index, item in enumerate(cast(list[object], value)):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in cast(dict[object, object], value).items():
            if not isinstance(key, str):
                raise CanonicalValueError(f"non-string mapping key at {path}")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise CanonicalValueError(f"unsupported JSON value {type(value).__name__} at {path}")


def clone_json_value(value: JsonValue) -> JsonValue:
    """Validate and detach a JSON value from caller-owned containers."""

    return cast(JsonValue, json.loads(canonical_json(value)))


def clone_json_object(value: Mapping[str, JsonValue]) -> JsonObject:
    """Validate and detach a JSON object from caller-owned containers."""

    cloned = clone_json_value(dict(value))
    if not isinstance(cloned, dict):
        raise CanonicalValueError("canonical state must be a JSON object")
    return cloned


def state_digest(state: Mapping[str, JsonValue]) -> str:
    """Return the SHA-256 digest of canonical state only."""

    serialized = canonical_json(dict(state)).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()
