"""Resource ownership and capability dependency gates."""

import ast
import sys

import pytest
from test_architecture import CORE_ROOT, REPOSITORY_ROOT, imported_names

from journeymap.core.modules import ModuleDependencyError, ModuleRegistry
from journeymap.modules.inventory import InventoryModule
from journeymap.modules.survival import SurvivalModule
from journeymap.modules.trade import TradeModule


@pytest.mark.parametrize(
    "module,extra",
    [
        ("inventory", set()),
        (
            "survival",
            {"journeymap.modules.inventory.models", "journeymap.modules.inventory.transitions"},
        ),
        (
            "trade",
            {
                "journeymap.modules.inventory.models",
                "journeymap.modules.inventory.transitions",
                "journeymap.modules.movement.perception",
            },
        ),
    ],
)
def test_resource_imports_follow_narrow_owned_contracts(module: str, extra: set[str]) -> None:
    root = REPOSITORY_ROOT / "src" / "journeymap" / "modules" / module
    for path in root.rglob("*.py"):
        for name in imported_names(path):
            assert name.split(".")[0] in sys.stdlib_module_names or (
                name.startswith(f"journeymap.modules.{module}.")
                or name in extra
                or (
                    name.startswith("journeymap.core.")
                    and name
                    not in {
                        "journeymap.core.kernel",
                        "journeymap.core.replay",
                        "journeymap.core.persistence",
                    }
                )
            )


def test_core_does_not_own_resource_state_and_contributors_have_safe_imports() -> None:
    for path in CORE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in {
                    "inventory",
                    "survival",
                    "trade",
                    "hunger",
                    "fatigue",
                    "wallets",
                    "offers",
                    "consumables",
                }
    path = REPOSITORY_ROOT / "src/journeymap/scenarios/alderwick/resource_contributors.py"
    assert imported_names(path) <= {"journeymap.core.canonical", "journeymap.core.observations"}


def test_resource_metadata_requires_inventory_and_spatial_dependencies() -> None:
    registry = ModuleRegistry()
    registry.register(SurvivalModule())
    with pytest.raises(ModuleDependencyError, match="inventory"):
        registry.validate_dependencies()
    registry.register(InventoryModule())
    assert registry.validate_dependencies() == ("inventory", "survival")
    registry.register(TradeModule())
    with pytest.raises(ModuleDependencyError, match="movement"):
        registry.validate_dependencies()
