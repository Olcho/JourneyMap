"""Executable M0 architecture dependency rules."""

import ast
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
CORE_ROOT = REPOSITORY_ROOT / "src" / "journeymap" / "core"
FORBIDDEN_CORE_IMPORTS = {
    "fastapi",
    "journeymap.adapters",
    "journeymap.bootstrap",
    "openai",
    "sqlalchemy",
    "sqlite3",
}


def imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def test_core_does_not_import_infrastructure_or_banned_frameworks() -> None:
    violations: list[str] = []
    for path in sorted(CORE_ROOT.rglob("*.py")):
        for imported in imported_names(path):
            if any(
                imported == forbidden or imported.startswith(f"{forbidden}.")
                for forbidden in FORBIDDEN_CORE_IMPORTS
            ):
                violations.append(f"{path.relative_to(CORE_ROOT)}: {imported}")

    assert violations == []


def test_project_has_no_runtime_dependencies() -> None:
    configuration = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert configuration["project"]["dependencies"] == []
