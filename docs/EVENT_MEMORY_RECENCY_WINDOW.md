# Event Memory fixed recency window correctness

## Scope and interface

`RecencyEventMemory(k: int = 1)` selects `context.prior[-k:]`, oldest to newest.
`type(k) is int` and `k > 0` are required; bool, non-int, zero and negative values
raise ValueError at construction. There is no arbitrary capacity ceiling. `k`
is a read-only public property. The default and explicit k=1 preserve Phase 1.

The policy ID remains `Recency-based Event Memory`. Instance versions are
`recency-event-memory-k1-1`, `recency-event-memory-k2-1` and
`recency-event-memory-k3-1`. Each manifest and decision carries the version,
so exports identify the exact window without a schema change.

## Protocol compatibility

| Protocol | Allowed policies | Correctness audit version |
|---|---|---|
| `alderwick-event-memory-phase1-1` | No Event Memory, Recency k=1 | `event-memory-phase1-correctness-1` |
| `alderwick-event-memory-recency-window-1` | Recency k=1, k=2, k=3 | `event-memory-recency-window-correctness-1` |

The generic policy accepts any positive k; this bounded correctness protocol only
admits 1/2/3. Unsupported combinations fail before Provider calls. The audit
reconstructs the declared policy and rejects incompatible or unknown versions,
even after resealing. Phase 1 cannot silently admit k=2/3.

Export schema remains **3**, Event Trace remains **v1**, and prompt remains
`alderwick-event-memory-decision-1`. Observation/prompt ordering, archive closure,
engine replay and schema 1/2 readers are unchanged. Schema 1 strict audit still
returns `NEW_PROVENANCE_REQUIRED`.

## Required delivery sequences

Each E is a closed experience, not a failed Provider attempt.

| Decision | k=1 | k=2 | k=3 |
|---|---|---|---|
| D1 | [] | [] | [] |
| D2 | [E1] | [E1] | [E1] |
| D3 | [E2] | [E1,E2] | [E1,E2] |
| D4 | [E3] | [E2,E3] | [E1,E2,E3] |
| D5 | [E4] | [E3,E4] | [E2,E3,E4] |
| D6 | [E5] | [E4,E5] | [E3,E4,E5] |

Provider/parser/pre-submit failure creates no Event Trace and consumes no slot.
Same-tick retries retain opportunity lineage without phantom experiences.
Engine REJECTED and receipt-bearing FAILED are closed experiences and occupy a
slot. E1 SUCCEEDED, E2 REJECTED, E3 SUCCEEDED therefore yields [E2,E3] for k=2.
The archive retains all experiences; window replacement does not delete records.

Every delivered selection is checked against retrieved IDs/count, canonical
serialized memory and UTF-8 bytes. The existing EventMemoryContext validates
same actor/run, eligible prior traces, closure time, observation and opportunity
ordering. Neither current nor future experiences become available through a
larger k. No World Truth or Research capability is added to the Controller.

## Offline CLI

The CLI always uses ProtocolFakeProvider and has no live mode. Existing no-memory
and recency-k1 choices retain Phase 1; k2/k3 use the new protocol. Direct
`run_trial(..., protocol_version=RECENCY_WINDOW_PROTOCOL_VERSION)` also supports k1.
Use a fresh output directory for each execution:

```powershell
python -m journeymap.examples.event_memory --memory no-memory --trial-id window-none --output trials/window-none
python -m journeymap.examples.event_memory --memory recency-k1 --trial-id window-k1 --output trials/window-k1
python -m journeymap.examples.event_memory --memory recency-k2 --trial-id window-k2 --output trials/window-k2
python -m journeymap.examples.event_memory --memory recency-k3 --trial-id window-k3 --output trials/window-k3
```

## Verification and limits

`tests/test_event_memory.py` adds 39 cases for construction, six-decision warm-up
and rolling windows, failure/retry and REJECTED slots, actor/run/time isolation,
provenance, protocol rejection, and full 24-tick Provider-free replay/audit.
The original 894 cases remain. Larger-than-history k=1000 selects only available
experiences. Existing Phase 1, engine FAILED and hardening tests remain intact.

This checkout initially had no `trials/` or historical raw exports. Schema 1/2
compatibility is tested using the existing synthetic schema 1 / engine 0.0.0
fixture and fresh hardening schema 2 fixture. Actual official and historical raw
artifact replay/hash equality cannot be certified here; no old artifacts were
modified. Do not confuse synthetic compatibility evidence with official replay.

The deterministic Provider verifies delivery correctness only. No actual model
performance comparison, selective memory, scoring, embeddings, reflection, RL,
new world features, API calls, commit, push or PR are part of this change.

## 2026-09-15 verification results

- Full pytest: **933 passed** (894 existing + 39 added), 94.91 seconds.
- Ruff check passed; format check: 126 files already formatted.
- Strict mypy: no issues in 113 source files. `git diff --check` passed.
- Full pytest used `--basetemp=trials/pytest-window-full` after the default temp
  directory denied access. One cache write PermissionError warning remained;
  all test assertions and fixture setup passed in the full run.
- CLI exports `trials/window-k1`, `trials/window-k2`, `trials/window-k3` and
  `trials/window-none`: all COMPLETED / HORIZON, tick 24, replay equality=true,
  audit INCLUDED, stored inclusion matches recomputed inclusion.
- New-protocol k=1/2/3 full trial/replay/audit also passed in the automated suite.
- Existing synthetic schema 1 and fresh hardening schema 2 were independently
  replayed/audited again: equality=true, tick 24. Schema 2 INCLUDED; schema 1
  EXCLUDED only for NEW_PROVENANCE_REQUIRED. Each fixture's 17 files had
  unchanged SHA-256 values before/after this independent verification.
- Historical official raw artifacts were absent and were not reconstructed or
  replaced. Actual historical artifact compatibility remains unverified locally.

The window delivery evidence is sufficient to prepare a first controlled
behavioral experiment. Before declaring the full requested historical gate
complete, rerun replay/audit/hash checks against the original raw exports on the
machine that retains them. No behavioral performance claim follows from these
fixture results.
