"""Collect E04 engine-backed synthetic witnesses. No external provider imports."""

import argparse
import hashlib
import json
import platform
from pathlib import Path
from typing import Any, cast

from journeymap_memoryreplica.projection import latest_facts, project_context, score_recall


def collect() -> dict[str, Any]:
    from journeymap.adapters.route_update import FakeProvider, Script
    from journeymap.experiments.route_update_session import Session

    records = []
    for condition in ("unchanged", "closed-unannounced", "closed-notified"):
        session = Session("memoryreplica-offline-" + condition)
        provider = FakeProvider(
            Script(
                tuple(
                    json.dumps({"action_type": "MOVE", "payload": {"route_id": route}})
                    for route in ("S-to-A", "A-to-C", "C-to-D", "D-to-A", "A-to-C", "C-to-G")
                )
            )
        )
        try:
            session.prepare(initial=True)
            session.finish(provider.generate(session.provider_request), None, initial=True)
            session.install_branch(condition)
            # End at C after five closed moves; then attempt C-to-G. Six probes.
            for index in range(6):
                prepared = session.prepare()
                semantic = cast(dict[str, Any], prepared["semantic_input"])
                observation = semantic["observation"]
                contexts = {p: project_context(semantic, p) for p in ("none", "recent", "full")}
                record: dict[str, Any] = {
                    "condition": condition,
                    "index": index,
                    "time": observation["time"],
                    "position": observation["position"],
                    "historical_e04_input": semantic,
                    "e04_current_initial_map_routes": sum(
                        len(r["claim"]["routes"])
                        for r in observation["knowledge"]
                        if r["claim"]["kind"] == "initial-map"
                    ),
                    "e04_current_status_records": sum(
                        r["claim"]["kind"] == "route-status" for r in observation["knowledge"]
                    ),
                    "prototype_inputs": contexts,
                    "prototype_summary": {},
                }
                for policy, context in contexts.items():
                    facts = latest_facts(context)
                    record["prototype_summary"][policy] = {
                        "memory_count": len(context["event_memory"]),
                        "route_ids": sorted({a["route_id"] for a in facts}),
                        "fact_count": len(facts),
                        "json_utf8_bytes": len(json.dumps(context, sort_keys=True).encode()),
                        "reference_extractor_self_check": score_recall(context, facts),
                    }
                records.append(record)
                if index == 5:
                    # A diagnostic observation after rejection, without another action.
                    break
                finished = session.finish(provider.generate(session.provider_request), None)
                record["following_scripted_receipt"] = cast(
                    list[dict[str, Any]], finished["steps"]
                )[-1]["receipt"]
                if session.stop != "RUNNING":
                    break
        finally:
            session.close()
    return {
        "schema": "memoryreplica-offline-prototype-1",
        "evidence_kind": "scripted_engine_and_projection_correctness_only",
        "external_api_calls": 0,
        "python": platform.python_version(),
        "records": records,
    }


def source_fingerprint(root: Path) -> dict[str, Any]:
    paths = [*sorted((root / "src").rglob("*.py")), root / "pyproject.toml"]
    hashes = {}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def validate_imports(root: Path) -> None:
    """Reject mixed package/source trees before running any experiment."""
    from importlib import import_module

    base = (root / "src").resolve()
    for module in (
        "journeymap",
        "journeymap_memoryreplica.projection",
        "journeymap.adapters.route_update",
        "journeymap.scenarios.route_update",
        "journeymap.experiments.route_update_session",
        "journeymap_memoryreplica.paired",
        "journeymap_memoryreplica.task",
    ):
        loaded = import_module(module).__file__
        if loaded is None or not Path(loaded).resolve().is_relative_to(base):
            raise ValueError("current source/import mismatch: " + module)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root", "--reference-root", dest="source_root", type=Path, required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import journeymap

    validate_imports(args.source_root)
    loaded = Path(journeymap.__file__).resolve()
    if not loaded.is_relative_to((args.source_root / "src").resolve()):
        raise ValueError("import path does not match requested current source")
    before = source_fingerprint(args.source_root)
    result = collect()
    after = source_fingerprint(args.source_root)
    if before != after:
        raise ValueError("source changed during probe")
    result["current_source_hashes"] = before
    result["current_source_root"] = str(args.source_root.resolve())
    result["source_unchanged"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "records": len(result["records"]),
                "source_files_checked": len(before),
                "source_unchanged": True,
                "external_api_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
