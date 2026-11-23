"""Propose, record offline preflight, audit, or explicitly approved paired probe run."""

import argparse
from pathlib import Path
from typing import cast
from uuid import uuid4

from journeymap.adapters.llm import strict_json
from journeymap.adapters.probe_execution import (
    PROTOCOL,
    ProbeApproval,
    binding,
    load_approval,
    object_,
    rows,
)
from journeymap.adapters.probe_journal import read_journal
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.experiments import life_reasoning_probe as probe
from journeymap.experiments.paid_probe import (
    RecordingProbeTransport,
    WireReply,
    audit_report,
    execute,
    make_report,
    proposal,
)


def read(path: Path) -> JsonObject:
    if path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("report too large")
    return object_(strict_json(path.read_text(encoding="utf-8")))


def write(path: Path, value: JsonObject) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(canonical_json(value) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("propose", "preflight", "run"):
        item = commands.add_parser(command)
        item.add_argument("--output", type=Path, required=True)
        if command != "preflight":
            item.add_argument("--registry", type=Path, required=True)
            item.add_argument("--run-id", required=True)
        if command == "run":
            item.add_argument("--approval", type=Path, required=True)
    item = commands.add_parser("audit")
    item.add_argument("report", type=Path)
    item = commands.add_parser("inspect")
    item.add_argument("--registry", type=Path, required=True)
    item.add_argument("--run-id", required=True)
    item.add_argument("--corpus", type=Path, required=True)
    item.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        raise PermissionError("v2 HTTP run disabled; use the continuation authority entry point")
    if args.command == "audit":
        print(canonical_json(audit_report(read(args.report))))
        return
    approval = load_approval(args.approval) if args.command == "run" else None
    args.output.mkdir(parents=True, exist_ok=False)
    if args.command == "propose":
        write(args.output / "proposal.json", proposal(args.run_id, args.registry))
        print(canonical_json({"authorized": False, "network_calls": 0}))
        return
    if args.command == "inspect":
        report = make_report(read(args.corpus), read_journal(args.registry, args.run_id))
    elif args.command == "preflight":
        corpus = probe.prepare()
        run_id, registry = str(uuid4()), args.output / "probe-runs.sqlite3"
        synthetic = ProbeApproval(
            str(uuid4()),
            "offline-fixture",
            "synthetic-probe-only",
            ("recording-only",),
            binding(run_id, registry, corpus),
        )
        replies = tuple(
            WireReply(
                cast(int, p["http_status"]),
                cast(str, p["raw_response"]),
                cast(int, p["elapsed_ms"]),
            )
            for p in rows(probe.mock_packets(corpus)["records"])
        )
        report = execute(run_id, registry, synthetic, transport=RecordingProbeTransport(replies))
    else:
        # The CLI never constructs a human approval, changes an approved body, or retries.
        # Persist reproducible inputs before any paid attempt, including on later exceptions.
        write(args.output / "corpus.json", probe.prepare())
        report = execute(args.run_id, args.registry, approval)
    write(args.output / "report.json", report)
    if not (args.output / "corpus.json").exists():
        write(args.output / "corpus.json", object_(report["corpus"]))
    print(
        canonical_json(
            {
                "protocol": PROTOCOL,
                "output": str(args.output),
                "accounting": report["accounting"],
                "actual_run": args.command == "run",
            }
        )
    )


if __name__ == "__main__":
    main()
