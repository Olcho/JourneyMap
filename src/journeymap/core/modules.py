"""Module metadata and startup dependency validation."""

from dataclasses import dataclass
from typing import Protocol


class ModuleRegistrationError(ValueError):
    """Raised when module metadata cannot be registered."""


class ModuleDependencyError(ValueError):
    """Raised when registered module dependencies are invalid."""


@dataclass(frozen=True, slots=True)
class ModuleMetadata:
    """Stable identity and direct dependencies for a module."""

    module_id: str
    version: str
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.module_id:
            raise ModuleRegistrationError("module_id must not be empty")
        if not self.version:
            raise ModuleRegistrationError(f"module {self.module_id!r} has an empty version")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ModuleRegistrationError(
                f"module {self.module_id!r} declares duplicate dependencies"
            )


class Module(Protocol):
    """Minimum module surface required during M0 composition."""

    @property
    def metadata(self) -> ModuleMetadata:
        """Return immutable module registration metadata."""


class ModuleRegistry:
    """Collect modules and validate their declared dependency graph."""

    def __init__(self) -> None:
        self._modules: dict[str, Module] = {}

    def register(self, module: Module) -> None:
        module_id = module.metadata.module_id
        if module_id in self._modules:
            raise ModuleRegistrationError(f"duplicate module_id: {module_id}")
        self._modules[module_id] = module

    @property
    def module_ids(self) -> tuple[str, ...]:
        """Return registered IDs in a stable order."""

        return tuple(sorted(self._modules))

    def validate_dependencies(self) -> tuple[str, ...]:
        """Validate dependencies and return a stable dependency-first order."""

        missing = sorted(
            (module_id, dependency)
            for module_id, module in self._modules.items()
            for dependency in module.metadata.dependencies
            if dependency not in self._modules
        )
        if missing:
            details = ", ".join(f"{module_id}->{dependency}" for module_id, dependency in missing)
            raise ModuleDependencyError(f"missing module dependencies: {details}")

        unresolved = {
            module_id: set(module.metadata.dependencies)
            for module_id, module in self._modules.items()
        }
        resolved: list[str] = []

        while unresolved:
            ready = sorted(
                module_id for module_id, dependencies in unresolved.items() if not dependencies
            )
            if not ready:
                cycle_members = ", ".join(sorted(unresolved))
                raise ModuleDependencyError(f"cyclic module dependencies: {cycle_members}")

            resolved.extend(ready)
            for module_id in ready:
                del unresolved[module_id]
            for dependencies in unresolved.values():
                dependencies.difference_update(ready)

        return tuple(resolved)
