"""Recompute the published tables from existing MemoryReplica v1 records."""

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from importlib import import_module
from pathlib import Path
from statistics import mean
from typing import Any

HERE = Path(__file__).resolve().parent
FOLLOWUP_RUN = "1587da9b-3d09-4ee8-89b5-6fc297dd7a48"
RECORDED = "RECORDED_MODEL_RESPONSES"


def read_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def followup_reports(path: Path) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        run = db.execute(
            "SELECT status,binding FROM probe_runs WHERE run_id=?", (FOLLOWUP_RUN,)
        ).fetchone()
        if run is None or run[0] != "FINISHED" or json.loads(run[1])["synthetic"] is not False:
            raise ValueError("expected the completed independent follow-up run")
        entries = db.execute(
            "SELECT payload FROM probe_entries WHERE run_id=? "
            "AND json_extract(payload,'$.stage')='CASE' ORDER BY ordinal",
            (FOLLOWUP_RUN,),
        )
        return [json.loads(payload)["data"]["result"] for (payload,) in entries]


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-results", type=Path, required=True)
    parser.add_argument("--followup-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    args = parser.parse_args()

    sys.path[:0] = [str(HERE / "followup"), str(HERE.parents[1] / "src")]
    adapter = import_module("memoryreplica_v1.adapter")
    aggregate = import_module("memoryreplica_v1.analysis").aggregate
    evaluate = import_module("memoryreplica_v1.evaluate")

    followup = HERE / "followup" / "protocol"
    protocol, _ = adapter.load_protocol(followup)
    settings = read_json(followup / "analysis.json")["bootstrap"]
    if (protocol["analysis_seed"], protocol["bootstrap_replicates"]) != (
        settings["seed"],
        settings["replicates"],
    ):
        raise ValueError("protocol and analysis bootstrap settings differ")
    cases_first = adapter.load_cases(HERE / "first" / "protocol", "main")
    cases_followup = adapter.load_cases(followup, "main")
    for cases in (cases_first, cases_followup):
        if Counter(case["family"] for case in cases) != {"ring": 20, "hub": 20, "bridge": 20}:
            raise ValueError("expected 20 worlds in each family for each batch")
    cases_pooled = cases_first + cases_followup
    if len({case["world_signature"] for case in cases_pooled}) != 120:
        raise ValueError("expected 120 distinct worlds")

    first = read_json(args.first_results)
    if first["evidence_kind"] != RECORDED:
        raise ValueError("expected saved model-response results from the first batch")
    reports_first = first["reports"]
    reports_followup = followup_reports(args.followup_db)

    contrasts = []
    conditions = []
    for sample, cases, reports in (
        ("first", cases_first, reports_first),
        ("followup", cases_followup, reports_followup),
        ("pooled", cases_pooled, reports_first + reports_followup),
    ):
        summary = aggregate(cases, reports, protocol, RECORDED)
        if not summary["batch_complete"]:
            raise ValueError(f"{sample} has unresolved or missing cases")
        primary = summary["primary_M_minus_R"]
        contrasts.append(
            {
                "sample": sample,
                "worlds": len(cases),
                "M_minus_R": primary["mean_difference"],
                "ci95_low": primary["ci95"][0],
                "ci95_high": primary["ci95"][1],
                "S_minus_F": summary["secondary_S_minus_F_descriptive"]["mean_difference"],
            }
        )
        for policy in adapter.POLICIES:
            stats = summary["policies"][policy]
            rows = [row for row in summary["rows"] if row["policy"] == policy]
            contexts = [case["contexts"][policy] for case in cases]
            paths = [
                evaluate.bounded_path(
                    evaluate.supplied_edges(ctx), ctx["current"]["position"], ctx["task"]["goal"]
                )["cost"]
                for ctx in contexts
            ]
            accuracies = [
                row["input_conditional_accuracy"]
                for row in rows
                if row["input_conditional_accuracy"] is not None
            ]
            conditions.append(
                {
                    "sample": sample,
                    "policy": policy,
                    "worlds": len(cases),
                    "F1_n": sum(row["world_edge_f1"] is not None for row in rows),
                    "edge_F1": stats["world_edge_f1_available_only"],
                    "E": stats["mean_E_available_only"],
                    "arrived": stats["success_count"],
                    "archived_edges": mean(len(ctx["archive"]) for ctx in contexts),
                    "input_has_goal_path": sum(cost is not None for cost in paths),
                    "input_fact_accuracy": mean(accuracies),
                }
            )

    args.output.mkdir(parents=True)
    write_csv(args.output / "contrasts.csv", contrasts)
    write_csv(args.output / "conditions.csv", conditions)
    print("Wrote summaries for 120 worlds and 600 policy-world evaluations.")


if __name__ == "__main__":
    main()
