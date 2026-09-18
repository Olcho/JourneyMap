"""Immutable task text for explicitly opted-in Event Memory experiments."""

from collections.abc import Callable
from dataclasses import dataclass

from journeymap.core.canonical import JsonObject, canonical_json


@dataclass(frozen=True, slots=True)
class PromptProfile:
    version: str
    instructions: str
    input_projection: Callable[[JsonObject], JsonObject] | None = None

    def __post_init__(self) -> None:
        if not self.version or not self.instructions:
            raise ValueError("prompt profile requires version and instructions")
        self.instructions.encode("utf-8")

    def render(self, inputs: JsonObject) -> str:
        return self.instructions + "\nINPUT_JSON\n" + canonical_json(inputs)
