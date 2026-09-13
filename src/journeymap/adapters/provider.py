"""Safe Provider contracts; no transport implementation or engine dependency."""

import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from journeymap.core.canonical import JsonObject, clone_json_object


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    prompt: str
    prompt_version: str
    model: str
    configuration_version: str
    parameters: JsonObject = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not str or not value
                for value in (
                    self.prompt,
                    self.prompt_version,
                    self.model,
                    self.configuration_version,
                )
            )
            or type(self.schema_version) is not int
            or self.schema_version != 1
        ):
            raise ValueError("invalid Provider request")
        object.__setattr__(self, "parameters", clone_json_object(self.parameters))


@dataclass(frozen=True, slots=True)
class RawModelResponse:
    text: str
    metadata: JsonObject = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.text) is not str
            or type(self.schema_version) is not int
            or self.schema_version != 1
        ):
            raise ValueError("invalid Provider response")
        object.__setattr__(self, "metadata", clone_json_object(self.metadata))


class Provider(Protocol):
    def generate(self, request: ProviderRequest) -> RawModelResponse: ...


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    name: str
    version: str
    kind: str = "fixture"
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if (
            any(type(value) is not str or not value for value in (self.name, self.version))
            or self.kind not in ("fixture", "live")
            or (self.kind == "fixture" and self.name != "fake")
            or (
                self.timeout_seconds is not None
                and (
                    isinstance(self.timeout_seconds, bool)
                    or not math.isfinite(self.timeout_seconds)
                    or self.timeout_seconds <= 0
                )
            )
        ):
            raise ValueError("invalid provider identity")


@runtime_checkable
class IdentifiedProvider(Provider, Protocol):
    @property
    def identity(self) -> ProviderIdentity: ...


def provider_identity(provider: Provider) -> JsonObject:
    """Adapter-owned identity; unidentified test doubles are always fixtures.

    This is a trusted Python composition contract, not adapter authentication.
    Response metadata and caller labels cannot change the implementation identity.
    """
    identity = (
        provider.identity
        if isinstance(provider, IdentifiedProvider)
        else ProviderIdentity("fake", "unversioned-fixture")
    )
    implementation = type(provider)
    return {
        "name": identity.name,
        "version": identity.version,
        "kind": identity.kind,
        "implementation": f"{implementation.__module__}.{implementation.__qualname__}",
        "timeout_seconds": identity.timeout_seconds,
        "response_model_policy": (
            "openai-alias-dated-snapshot-1"
            if identity.kind == "live"
            else "exact-requested-model-1"
        ),
    }


def response_identity_matches(identity: JsonObject, model: str, metadata: JsonObject) -> bool:
    expected = {
        "provider": identity["name"],
        "adapter_version": identity["version"],
    }
    # Live adapters must return all three. Minimal old test doubles may omit them;
    # any supplied identity must still agree with the local configuration.
    if not all(
        metadata.get(key) == value
        for key, value in expected.items()
        if key in metadata or identity["kind"] == "live"
    ):
        return False
    returned = metadata.get("model")
    if returned == model:
        return True
    if identity["kind"] == "fixture":
        return "model" not in metadata
    # A versioned naming rule, not a claim that a particular snapshot exists.
    # A pinned request cannot silently change snapshots or revert to an alias.
    if (
        identity.get("response_model_policy") != "openai-alias-dated-snapshot-1"
        or not isinstance(returned, str)
        or re.search(r"-\d{4}-\d{2}-\d{2}$", model)
        or not returned.startswith(model + "-")
    ):
        return False
    suffix = returned[len(model) + 1 :]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", suffix) is None:
        return False
    try:
        date.fromisoformat(suffix)
    except ValueError:
        return False
    return True


class ProviderFailure(RuntimeError):
    """Allowlisted failure provenance, never an HTTP body/header/exception string."""

    def __init__(self, kind: str, http_status: int | None = None) -> None:
        if kind not in {
            "CREDENTIAL_UNAVAILABLE",
            "HTTP_ERROR",
            "TIMEOUT",
            "TRANSPORT_ERROR",
            "SECRET_ECHO",
            "RESPONSE_TOO_LARGE",
        }:
            raise ValueError("invalid provider failure kind")
        self.kind = kind
        self.http_status = http_status
        super().__init__(f"PROVIDER_{kind}")
