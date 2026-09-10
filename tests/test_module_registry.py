"""M0 module dependency rule tests."""

from dataclasses import dataclass

import pytest

from journeymap.core.modules import (
    ModuleDependencyError,
    ModuleMetadata,
    ModuleRegistrationError,
    ModuleRegistry,
)


@dataclass(frozen=True)
class StubModule:
    metadata: ModuleMetadata


def module(
    module_id: str,
    *,
    dependencies: tuple[str, ...] = (),
) -> StubModule:
    return StubModule(ModuleMetadata(module_id, "0.0.0", dependencies))


def test_registry_returns_stable_dependency_first_order() -> None:
    registry = ModuleRegistry()
    registry.register(module("trade", dependencies=("inventory",)))
    registry.register(module("knowledge"))
    registry.register(module("inventory"))

    assert registry.validate_dependencies() == ("inventory", "knowledge", "trade")


def test_registry_rejects_duplicate_module_id() -> None:
    registry = ModuleRegistry()
    registry.register(module("movement"))

    with pytest.raises(ModuleRegistrationError, match="duplicate module_id: movement"):
        registry.register(module("movement"))


def test_registry_rejects_missing_dependency() -> None:
    registry = ModuleRegistry()
    registry.register(module("trade", dependencies=("inventory",)))

    with pytest.raises(
        ModuleDependencyError,
        match="missing module dependencies: trade->inventory",
    ):
        registry.validate_dependencies()


def test_registry_rejects_dependency_cycle() -> None:
    registry = ModuleRegistry()
    registry.register(module("knowledge", dependencies=("social",)))
    registry.register(module("social", dependencies=("knowledge",)))

    with pytest.raises(
        ModuleDependencyError,
        match="cyclic module dependencies: knowledge, social",
    ):
        registry.validate_dependencies()
