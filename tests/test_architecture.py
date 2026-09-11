"""Executable M0 architecture dependency rules."""

import ast
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
CORE_ROOT = REPOSITORY_ROOT / "src" / "journeymap" / "core"
FORBIDDEN_CORE_IMPORTS = {
    "datetime",
    "fastapi",
    "journeymap.adapters",
    "journeymap.application",
    "journeymap.bootstrap",
    "journeymap.modules",
    "openai",
    "random",
    "secrets",
    "sqlalchemy",
    "sqlite3",
    "time",
    "uuid",
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


def test_core_does_not_define_or_reference_movement_domain_state() -> None:
    forbidden_types = {"Location", "Route", "ActorPosition", "MoveHandler", "MovementModule"}
    forbidden_state_keys = {"movement", "locations", "routes", "positions", "route_id"}
    for path in sorted(CORE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.Name, ast.Attribute)):
                name = (
                    node.name
                    if isinstance(node, ast.ClassDef)
                    else (node.id if isinstance(node, ast.Name) else node.attr)
                )
                assert name not in forbidden_types, f"{path}: {name}"
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in forbidden_state_keys, f"{path}: {node.value}"


def test_movement_imports_only_core_its_own_module_and_standard_library() -> None:
    import sys

    root = REPOSITORY_ROOT / "src" / "journeymap" / "modules" / "movement"
    for path in sorted(root.rglob("*.py")):
        for imported in imported_names(path):
            assert (
                imported.split(".")[0] in sys.stdlib_module_names
                or imported.startswith("journeymap.core.")
                or imported.startswith("journeymap.modules.movement.")
            ), f"{path}: {imported}"


def test_core_does_not_own_knowledge_semantics() -> None:
    forbidden = {
        "KnowledgeRecord",
        "KnowledgeLedger",
        "subject_ref",
        "predicate",
        "confidence",
        "supersedes_id",
    }
    for path in sorted(CORE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.Name, ast.Attribute)):
                name = (
                    node.name
                    if isinstance(node, ast.ClassDef)
                    else (node.id if isinstance(node, ast.Name) else node.attr)
                )
                assert name not in forbidden, f"{path}: {name}"
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in forbidden, f"{path}: {node.value}"


def test_controller_surface_has_no_engine_research_or_adapter_imports() -> None:
    import sys

    for imported in imported_names(CORE_ROOT / "controller.py"):
        assert imported.split(".")[0] in sys.stdlib_module_names or imported in {
            "journeymap.core.handlers",
            "journeymap.core.observations",
        }


def test_knowledge_has_no_world_engine_movement_or_infrastructure_dependency() -> None:
    import sys

    root = REPOSITORY_ROOT / "src" / "journeymap" / "modules" / "knowledge"
    for path in sorted(root.rglob("*.py")):
        for imported in imported_names(path):
            assert imported.split(".")[0] in sys.stdlib_module_names or imported in {
                "journeymap.core.canonical",
                "journeymap.core.observations",
                "journeymap.core.modules",
                "journeymap.modules.knowledge.records",
            }, f"{path}: {imported}"
