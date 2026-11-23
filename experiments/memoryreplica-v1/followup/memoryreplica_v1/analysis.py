"""Prespecified paired summaries and fixed-layout research figures."""

from collections import Counter
import csv
import json
import math
from pathlib import Path
import random
from statistics import mean

from .adapter import POLICIES, digest
from .evaluate import supplied_edges, well_typed

FAMILIES = ("ring", "hub", "bridge")


def quantile(values, probability):
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    return values[lower] + (values[math.ceil(position)] - values[lower]) * (position - lower)


def paired_interval(groups, protocol):
    rng = random.Random(protocol["analysis_seed"])
    estimate = mean(mean(groups[family]) for family in FAMILIES)
    samples = sorted(mean(mean(rng.choices(groups[f], k=len(groups[f]))) for f in FAMILIES)
                     for _ in range(protocol["bootstrap_replicates"]))
    interval = [quantile(samples, .025), quantile(samples, .975)]
    return {"mean_difference": estimate, "ci95": interval,
            "n_worlds": sum(map(len, groups.values())),
            "replicates": protocol["bootstrap_replicates"], "seed": protocol["analysis_seed"],
            "family_weights": {f: 1 / 3 for f in FAMILIES},
            "degenerate_interval": interval[0] == interval[1],
            "equivalence_established": False}


def flat_row(report):
    recon = report["reconstruction"] or {}
    steps = report["action_steps"]
    rejected = [s for s in steps if s.get("receipt") and s["receipt"]["status"] != "SUCCEEDED"]
    reject_keys = [json.dumps(s["action"], sort_keys=True) for s in rejected]
    position = report["baseline"]["start"]
    visited = {position}
    revisit_count = 0
    for step in steps:
        if (step.get("action") and step["action"]["action_type"] == "MOVE"
                and step.get("receipt") and step["receipt"]["status"] == "SUCCEEDED"):
            position = step["observation_after"]["position"]
            revisit_count += position in visited
            visited.add(position)
    known_cost = recon.get("input_path", {}).get("cost")
    return {"case_id": report["case_id"], "family": report["family"], "policy": report["policy"],
            "provided_result": True,
            "stop": report["stop"], "success": report["success"], "E": report["score"],
            "decisions": report["final"]["decisions"], "ticks": report["final"]["elapsed_ticks"],
            "rejected_actions": len(rejected),
            "repeated_identical_rejected_actions": len(reject_keys) - len(set(reject_keys)),
            "revisits_not_automatically_errors": revisit_count,
            "reconstruction_status": recon.get("status"),
            **{k: recon.get(k) for k in ("world_edge_f1", "observed_coverage", "retention_loss_count",
                "input_conditional_accuracy", "input_missing_facts", "input_unknown_facts",
                "input_wrong_facts", "provenance_accuracy", "correct_but_unprovided_facts",
                "archive_mean_age_ticks")},
            "input_path_cost": known_cost,
            "failed_despite_initial_known_route": (report["success"] is False if known_cost is not None
                                                   and report["success"] is not None else None),
            "tick_difference_vs_initial_known_route": (report["final"]["elapsed_ticks"] - known_cost
                                                       if report["success"] and known_cost is not None else None),
            "claimed_path_cost": recon.get("claimed_path", {}).get("cost"),
            "valid_claimed_path_cost": recon.get("truth_validated_claimed_path", {}).get("cost")}


def aggregate(cases, reports, protocol, evidence_kind):
    expected = {(c["case_id"], p) for c in cases for p in POLICIES}
    case_by_id = {c["case_id"]: c for c in cases}
    indexed, response_ids = {}, set()
    for report in reports:
        key = (report["case_id"], report["policy"])
        if key not in expected or key in indexed:
            raise ValueError("unexpected or duplicate environment-policy result")
        case = case_by_id[key[0]]
        if report["input_case_sha256"] != digest(case) or report["family"] != case["family"]:
            raise ValueError("result does not belong to frozen input")
        for packet in report["packets"]:
            response_id = packet["outcome"]["response_id"]
            if response_id:
                if response_id in response_ids:
                    raise ValueError("duplicate response ID across batch")
                response_ids.add(response_id)
        indexed[key] = report
    absent = sorted(expected - set(indexed))
    unresolved = sorted(key for key, report in indexed.items() if report["score"] is None)
    complete = not absent and not unresolved
    rows = [flat_row(indexed[key]) for key in sorted(indexed)]
    template = rows[0] if rows else {"case_id": None, "family": None, "policy": None,
                                   "provided_result": None, "stop": None, "success": None, "E": None,
                                   "world_edge_f1": None}
    for case_id, policy in absent:
        rows.append({**dict.fromkeys(template), "case_id": case_id, "policy": policy,
                     "family": case_by_id[case_id]["family"], "provided_result": False,
                     "stop": "NOT_ATTEMPTED"})
    rows.sort(key=lambda row: (row["case_id"], row["policy"]))
    result = {"schema": "memoryreplica-v1-summary-1", "evidence_kind": evidence_kind,
              "is_llm_behavioral_evidence": evidence_kind == "RECORDED_MODEL_RESPONSES",
              "expected_episodes": len(expected), "received_episodes": len(reports),
              "analysis_unit": "world", "n_worlds": len(cases),
              "batch_complete": complete,
              "missing_results": [dict(case_id=c, policy=p) for c, p in absent],
              "unresolved_results": [dict(case_id=c, policy=p) for c, p in unresolved],
              "primary_M_minus_R": None, "secondary_S_minus_F_descriptive": None,
              "policies": {}, "rows": rows,
              "interpretation_limits": [
                  "One model draw per condition per world; no within-world variance estimate.",
                  "Intervals generalize only to eligible draws from the three fixed generators.",
                  "A degenerate bootstrap interval does not establish population equivalence.",
                  "Reference or synthetic results are not LLM performance measurements."]}
    for policy in POLICIES:
        own = [r for r in rows if r["policy"] == policy]
        resolved = [r for r in own if r["E"] is not None]
        model_recon = [r for r in own if r["world_edge_f1"] is not None]
        result["policies"][policy] = {
            "expected": len(cases), "received": sum(r["provided_result"] for r in own), "resolved": len(resolved),
            "success_count": sum(r["success"] is True for r in resolved),
            "mean_E_available_only": mean(r["E"] for r in resolved) if resolved else None,
            "world_edge_f1_available_only": mean(r["world_edge_f1"] for r in model_recon) if model_recon else None,
            "stop_counts": dict(Counter(r["stop"] for r in own)),
            "descriptive_only_if_incomplete": not complete}
    if complete:
        groups = {family: [] for family in FAMILIES}
        sf = []
        for case in cases:
            get = lambda policy: indexed[(case["case_id"], policy)]["score"]
            groups[case["family"]].append(get("M") - get("R"))
            sf.append(get("S") - get("F"))
        if not all(groups.values()) or len({len(v) for v in groups.values()}) != 1:
            raise ValueError("expected equal fixed family sample sizes")
        result["primary_M_minus_R"] = paired_interval(groups, protocol)
        result["secondary_S_minus_F_descriptive"] = {"mean_difference": mean(sf), "n_worlds": len(sf)}
    return result


def write_summary(summary, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    if summary["rows"]:
        with (directory / "error_and_behavior.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(summary["rows"][0]))
            writer.writeheader()
            writer.writerows(summary["rows"])


def render_case(case, report, output, evidence_kind):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch

    nodes = sorted(case["nodes"])
    positions = {n: (math.cos(2 * math.pi * i / len(nodes)), math.sin(2 * math.pi * i / len(nodes)))
                 for i, n in enumerate(nodes)}
    provided = supplied_edges(report["input_context"])
    recon = report["reconstruction"] or {}
    claimed = [r for r in recon.get("claimed_graph", []) if well_typed(r)]
    traversed = {s["action"]["payload"]["route_id"] for s in report["action_steps"]
                 if s.get("action") and s["action"]["action_type"] == "MOVE"
                 and s.get("receipt") and s["receipt"]["status"] == "SUCCEEDED"}
    panels = [("World truth", case["world_truth"]), ("Retained + current", provided),
              ("Reconstructed", claimed), ("Executed route", case["world_truth"])]
    fig, axes = plt.subplots(1, 4, figsize=(15, 4.5))
    for index, (ax, (title, edges)) in enumerate(zip(axes, panels, strict=True)):
        for edge in edges:
            if edge["origin"] not in positions or edge["destination"] not in positions:
                continue
            highlight = index == 3 and edge["route_id"] in traversed
            color = "#c44924" if highlight else "#93a1ad" if index == 3 else "#486c8b"
            arrow = FancyArrowPatch(positions[edge["origin"]], positions[edge["destination"]],
                                    arrowstyle="-|>", mutation_scale=10, shrinkA=13, shrinkB=13,
                                    connectionstyle="arc3,rad=0.09", color=color,
                                    linewidth=2.2 if highlight else .9,
                                    alpha=1 if highlight or index != 3 else .4)
            ax.add_patch(arrow)
        for node, (x, y) in positions.items():
            color = "#d5eac7" if node == case["goal"] else "#f4dab8" if node == case["start"] else "white"
            ax.scatter([x], [y], s=470, color=color, edgecolors="#29455e", zorder=3)
            ax.text(x, y, node, fontsize=8, ha="center", va="center", zorder=4)
        ax.set_title(title)
        ax.set(xlim=(-1.35, 1.35), ylim=(-1.35, 1.35), aspect="equal")
        ax.axis("off")
    label = "RECORDED MODEL RESPONSE" if evidence_kind == "RECORDED_MODEL_RESPONSES" else "OFFLINE FIXTURE - NOT LLM RESULTS"
    fig.suptitle(f"{label}\n{case['case_id']} / {report['policy']} | orange: start, green: goal", fontsize=12)
    fig.text(.5, .025, "Same reference node layout in every panel; nodes are anchors, not claimed model knowledge.\n"
             "Arrows show direction; red highlights executed moves.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, .10, 1, .9))
    fig.savefig(output, dpi=160)
    plt.close(fig)
