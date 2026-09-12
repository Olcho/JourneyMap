"""Safe Provider contracts; no transport implementation or engine dependency."""

from dataclasses import dataclass, field
from typing import Protocol

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
