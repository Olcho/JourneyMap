"""Application composition root."""

from collections.abc import Iterable
from pathlib import Path

from journeymap.adapters.sqlite import SQLitePersistence
from journeymap.core.kernel import SimulationKernel
from journeymap.core.modules import Module, ModuleRegistry


def create_kernel(
    *,
    database: str | Path = ":memory:",
    modules: Iterable[Module] = (),
) -> SimulationKernel:
    """Compose an unbooted kernel from explicit in-process dependencies."""

    registry = ModuleRegistry()
    for module in modules:
        registry.register(module)
    return SimulationKernel(registry, SQLitePersistence(database))
