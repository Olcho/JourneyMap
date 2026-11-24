"""The only current HTTP probe runner: verified history, combined approval, durable claim."""

from pathlib import Path

from journeymap.adapters.probe_continuation import (
    PROTOCOL,
    Anchor,
    ContinuationApproval,
    FollowupLedger,
    exposure,
)
from journeymap.adapters.probe_execution import object_, rows, same
from journeymap.adapters.probe_lineage import (
    ContinuationJournal,
    check_snapshot,
    journal_from_run,
    read_snapshot,
)
from journeymap.core.canonical import JsonObject
from journeymap.experiments import life_reasoning_probe as probe
from journeymap.experiments import paid_probe as paid


def make_report(snapshot: JsonObject, frozen_source: Path) -> JsonObject:
    anchor, _, reduced = check_snapshot(snapshot, frozen_source)
    runs = rows(snapshot["runs"])
    if len(runs) != 1 or reduced is None:
        raise ValueError("no committed successor to report")
    run = runs[0]
    corpus = object_(run["corpus"])
    if not same(corpus, probe.prepare()):
        raise ValueError("continuation source/corpus mismatch")
    return {
        "protocol": PROTOCOL,
        "corpus": corpus,
        "journal": journal_from_run(run),
        "accounting": {
            k: v
            for k, v in reduced.items()
            if k not in {"packets", "outcomes", "approval", "diagnostics"}
        },
        "transport_diagnostics": reduced["diagnostics"],
        "evaluation": paid.summarize(corpus, reduced),
        "lineage_accounting": exposure(object_(reduced["ledger"])),
        "authority": snapshot,
        "latest_recorded_checkpoint": anchor.data,
        "audit_scope": (
            "data-only lineage/history/journal/engine consistency, "
            "not current execution eligibility or invoice"
        ),
        "live_authorization_independently_verified": False,
    }


def execute(
    path: Path,
    anchor: Anchor | None,
    approval: ContinuationApproval | None,
    *,
    transport: paid.RecordingProbeTransport | paid.HTTPProbeTransport | None = None,
) -> JsonObject:
    if anchor is None or approval is None:
        raise PermissionError(
            "explicit policy approval and trusted latest authority checkpoint required"
        )
    if transport is not None and type(transport) not in {
        paid.RecordingProbeTransport,
        paid.HTTPProbeTransport,
    }:
        raise PermissionError("unreviewed continuation transport")
    wire = transport if transport is not None else paid.HTTPProbeTransport()
    if wire.claimed:
        raise PermissionError("transport already consumed")
    corpus = probe.prepare()
    journal = ContinuationJournal(
        path, anchor, approval, corpus, synthetic=type(wire) is paid.RecordingProbeTransport
    )
    runtime = Path(str(journal.history_bundle["runtime_path"]))
    paid.run_admitted(journal, wire, corpus, journal.bound, FollowupLedger())
    return make_report(read_snapshot(path), runtime)


def audit_report(report: JsonObject, frozen_source: Path) -> JsonObject:
    rebuilt = make_report(object_(report["authority"]), frozen_source)
    if not same(report, rebuilt):
        raise ValueError("continuation report differs from data-only replay")
    return {
        "data_only_equal": True,
        "provider_recalled": False,
        "status": object_(rebuilt["accounting"])["status"],
        "lineage_total_cost_nano_usd": None,
        "current_execution_eligibility_verified": False,
        "live_authorization_independently_verified": False,
    }
