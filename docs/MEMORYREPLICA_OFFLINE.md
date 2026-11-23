# MemoryReplica: stable A13 → B offline contract

2026-10-30 KST. This is an additional research task over the current E04 runtime.
Historical E04 profiles, initial knowledge, results, live gates and approvals retain
their original meaning. No HTTP or live Provider is connected to this task.

## Source and ownership

The integration uses the existing `codex/experiment03-live-preparation` worktree,
HEAD `557fb3df10149d7b7738137b7ac1038dfba24e84`, including its uncommitted E04 code.
All 140 source/configuration hashes recorded in the supplied Task B evidence matched
the current files before integration. The supplied package's 13 manifest entries
were verified. No historical source was extracted over this worktree.

- `journeymap_memoryreplica/projection.py`: local-experience projection and evidence recall scoring.
- `journeymap_memoryreplica/paired.py`: separate reconstruction/action requests and
  inert `OfflineReplies`, bounded actions through the existing GamePort/Engine.
- `journeymap_memoryreplica/task.py`: stable B task diagnostics and six witnesses.
- `journeymap_memoryreplica/probe.py`: inherited input-boundary probes and current
  source fingerprints/import validation. It requires no historical MANIFEST.json.

The extension lives in the sibling `src/journeymap_memoryreplica` package. Existing
E04 code never imports it. The frozen E04 source scope remains exactly
`src/journeymap/**/*.py+pyproject.toml`; its seven-file v3→v4 compatibility allowlist
is unchanged. MemoryReplica exports separately fingerprint **both** packages.
The initial attempt to add files inside `journeymap` was rejected by that gate;
the package separation preserves the gate rather than widening it.

These modules use the existing E04 Session, semantic allowlist, archive, movement
handlers and deterministic mutation boundary. Core, Modules, E04 profiles and
scenario are unchanged. Dynamic JSON at the adapter/research boundary is explicitly
typed; untrusted action JSON still passes the existing strict parser and authority
validation. No new dependency, database schema or canonical memory state is added.

## Input and execution

Scripted preparation follows S→A→C→D→A, including E04's external advance from tick
2 to 3. Evaluation begins at A/tick 13. The stable B task varies only memory access:
none, the latest three closed experiences, or all experiences. The complete
canonical world and full prepared experience are common across the three policies.
The preparation fixture is not model training or an observed model decision.

All MemoryReplica inputs exclude the authored initial map, including maps inside
past experiences. Current local exits and newly received route updates remain.
Update acquisition is determined before memory selection, so forgotten updates
cannot reappear from cumulative knowledge. The same policy is reapplied after each
action. The existing E04 semantic projection removes actor/run/record authority IDs.
Same-tick update acquisition still uses E04 `learned_at`; this version does not add
a general event-sequence acquisition model for multiple new notices at one tick.

Both first requests are serialized independently from the same context before
either response is read. The MemoryReplica request profile is
`memoryreplica-task-offline-1`. Reconstruction text, recall scores, policy/condition
labels, source hashes, truth and optimal paths never enter action requests.
The only accepted providers are fresh, separate, exact `OfflineReplies` instances
containing immutable tuples of strings. There is no live runner or fallback.

Each evaluation has at most six actions and 24 ticks after the shared checkpoint.
Invalid JSON/schema, Engine rejection, fixture exhaustion, time/decision limit,
recording error and actual arrival are recorded separately. Time bounds are checked
before submission. Reconstruction failure cannot repair or change action input.
Preparation and evaluation actions use validated handlers, not direct state writes.

## Research interpretation

| Input memory | Initially supported best path to B | Cost |
| --- | --- | --- |
| none | No complete supported path | null |
| recent three | A→C→B (A→D→B also costs 8) | 8 ticks |
| full | A→S→B | 4 ticks |

The true world optimum is 4 ticks for every policy. `null` means insufficient
initial supplied evidence, not unreachable or guaranteed failure. Revisiting S
reveals S→B; the none condition can execute the four-tick route. Both short and
long paths are executed in every policy as common scripted witnesses. No policy
selects the successful fixture for its own arm.

The graph diagnostic requires explicit origin/destination/cost/passability and
does not infer reverse routes from names. It is research output only. Latest
supported recall, older supported recall, unknown, contradiction, unsupported
claim and omission are separate. Current-world mismatch is a separate diagnostic
and cannot overwrite historical recall correctness.

The G control and inherited closed/notified probes remain regression witnesses.
They do not complete a new failure/update environment study. This stable B case
does not establish LLM memory use, behavioral effects, generalization, a second
map, place-name substitutions, or statistical significance. Tests prove program
correctness only. Actual calls, model/inference/input/output budgets and repeated
behavioral trials require a separate research decision and explicit authorization.

## Offline commands

Use Python 3.12 and dev packages, with PYTHONPATH set to this worktree's `src`.
Run from this worktree. Choose a fresh output path; exports use exclusive creation.

```powershell
python -m pytest tests -k memoryreplica
python -m journeymap_memoryreplica.task --source-root . --output <fresh-path>/task-check.json
python -m journeymap_memoryreplica.probe --source-root . --output <fresh-path>/input-probe.json
python -m journeymap_memoryreplica.paired --source-root . --output <fresh-path>/paired-check.json
ruff check .
ruff format --check .
mypy
pytest
git diff --check
```

The source-root check verifies the loaded package, MemoryReplica and reused E04
modules against the requested current tree. Exports fingerprint current source
before/after running, rather than relabeling it as the old ZIP's source. These
unsigned local hashes establish consistency/preservation within the stated scope;
they are not a complete historical audit or external provenance attestation.

See [integration results](MEMORYREPLICA_REPORT_2026-10-30.md) for actual verification
counts, paths, preservation inventory and limitations.


## MemoryReplica dynamic Actual (2026-11-02)

The dynamic saved-frame module and Actual coordinator are implemented as of 2026-11-02. `dynamic.reconstruct_prefix/advance_case` rebuild next requests or terminal Engine results from recorded frames. Actual owns approval, the existing lineage/probe SQLite tables, the existing Ledger settlement, and HTTPProbeTransport; the research child imports only this worktree. Both inventories and the Python executable are bound. Only validated next_wire.body is sent. Complete B authority is required; import-only or stale anchors cannot claim a batch. Source-bound replay is not paid resume. No API call or key read was used for integration. The 9-case/63-request/6-action/24-tick/zero-retry/$22 plan remains an unapproved candidate. See the completion evidence at `C:/Users/<user>/Documents/JourneyMap/artifacts/memoryreplica-dynamic-20261102/REPORT.md`.
