"""Minimal simulation kernel lifecycle for the M0 foundation."""

from journeymap.core.modules import ModuleRegistry
from journeymap.core.persistence import Persistence


class SimulationKernel:
    """Own boot validation without introducing simulation-domain behavior."""

    def __init__(self, modules: ModuleRegistry, persistence: Persistence) -> None:
        self._modules = modules
        self._persistence = persistence
        self._is_booted = False

    @property
    def is_booted(self) -> bool:
        return self._is_booted

    @property
    def module_ids(self) -> tuple[str, ...]:
        return self._modules.module_ids

    def boot(self) -> None:
        if self._is_booted:
            raise RuntimeError("simulation kernel is already booted")
        self._modules.validate_dependencies()
        self._persistence.initialize()
        self._is_booted = True

    def close(self) -> None:
        if not self._is_booted:
            return
        self._persistence.close()
        self._is_booted = False
