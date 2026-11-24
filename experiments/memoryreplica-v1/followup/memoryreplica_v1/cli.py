"""Offline commands and stdin/stdout boundary for an existing Actual runner."""

import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-src", type=Path, required=True)
    parser.add_argument("--protocol-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("development", "main"), required=True)
    parser.add_argument("--output", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    one = sub.add_parser("advance")
    one.add_argument("--case-id", required=True)
    one.add_argument("--policy", choices=list("NSFMR"), required=True)
    one.add_argument("--saved", type=Path)
    sub.add_parser("bridge")
    summary = sub.add_parser("summarize")
    summary.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(args.source_src.resolve()))
    from .adapter import POLICIES, advance, digest, load_cases, load_protocol, make_wire
    from .analysis import aggregate, write_summary

    protocol, prompts = load_protocol(args.protocol_dir)
    cases = load_cases(args.protocol_dir, args.phase)
    case_by_id = {c["case_id"]: c for c in cases}
    if args.command == "prepare":
        plan = []
        for index, case in enumerate(cases):
            offset = index % 5
            for policy in POLICIES[offset:] + POLICIES[:offset]:
                current = advance(case, policy, [], protocol, prompts)
                plan.append({"case_id": case["case_id"], "episode_id": current["episode_id"], "policy": policy,
                             "input_case_sha256": digest(case),
                             "first_reconstruction_wire": current["next_wire"],
                             "first_action_wire": make_wire(case["contexts"][policy], "action", protocol, prompts)})
        result = {"evidence_kind": "PREPARED_INPUTS_NOT_MODEL_RESULTS", "phase": args.phase,
                  "episodes": len(plan), "max_requests": len(plan) * 7,
                  "external_api_calls": 0, "plan": plan}
    elif args.command in ("advance", "bridge"):
        if args.command == "bridge":
            incoming = json.load(sys.stdin)
            case_id, policy, saved = incoming["case_id"], incoming["policy"], incoming["saved"]
        else:
            case_id, policy = args.case_id, args.policy
            saved = json.loads(args.saved.read_text()) if args.saved else []
        result = advance(case_by_id[case_id], policy, saved, protocol, prompts)
    else:
        if not args.output:
            parser.error("summarize needs --output directory")
        data = json.loads(args.results.read_text())
        if data["evidence_kind"] not in {"OFFLINE_ENGINE_FIXTURE", "RECORDED_MODEL_RESPONSES"}:
            raise ValueError("explicit result origin required")
        # Reuse the same stepper, without sending, to obtain scores from saved data.
        for report in data["reports"]:
            rebuilt = advance(case_by_id[report["case_id"]], report["policy"], report["saved"], protocol, prompts)
            if rebuilt != report:
                raise ValueError("saved report differs from engine reconstruction")
        result = aggregate(cases, data["reports"], protocol, data["evidence_kind"])
        write_summary(result, args.output)
        print(json.dumps({"batch_complete": result["batch_complete"],
                          "episodes": result["received_episodes"],
                          "evidence_kind": result["evidence_kind"]}))
        return
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
        print(json.dumps({"output": str(args.output), "external_api_calls": 0}))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
