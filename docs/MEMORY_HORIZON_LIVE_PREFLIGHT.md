# Experiment 03 — Memory Horizon Live Pilot Preflight

This is a recording-mock-only preflight, not a behavioral experiment. No network,
API key, real OpenAI Provider, live-capable CLI, automatic resume, or new memory
policy is part of this implementation. The earlier
`MEMORY_HORIZON_READINESS_AUDIT.md` remains an unchanged historical report.

## Versioned boundaries

| Contract | Identity |
|---|---|
| Study plan | `memory-horizon-pilot-study-1` |
| Execution protocol | `alderwick-memory-horizon-live-1` |
| Audit | `memory-horizon-live-preflight-audit-1` |
| Trial evidence | `memory-horizon-pilot-evidence-1` |
| Cohort schema | `1` |
| Prompt | unchanged `alderwick-memory-horizon-decision-2` |
| Wire schema | `memory-horizon-decision-1` |
| Request configuration | `memory-horizon-responses-preflight-1` |
| Mock | `memory-horizon-recording-mock-1` |
| Journal | `memory-horizon-attempt-journal-1` |

These new preflight identities do not replace offline v1/v2, historical M8,
Event Memory Phase 1, export schema 3, or the general DecisionCandidate schema.
The old development WIP export is retained unchanged; it lacks the completed
preflight's frozen-plan seal, actual transport-body capture and cohort evidence.
It is not migrated or relabeled as validated current evidence.

## Plan, allocation and lifecycle

There are 12 original allocations: three blocks, each containing exactly one
No Event Memory, Recency k=1, k=2 and k=3 trial. A separately specified integer
researcher seed drives `random.Random(...).shuffle` within each block. Engine
seed and allocation seed are separate; neither is an LLM sampling guarantee.
IDs/order/block/condition are researcher-only. Tick 0–24, unit WAIT and the
unchanged semantic prompt apply throughout; task completion does not stop a run.

`run_mock_trial` owns a new Provider, recording transport, Controller/archive,
Monitor, Kernel and Application for every allocation. Both Provider and transport
reject reuse. Only concrete mock instances and inert `MockOutcome` fixtures are
accepted. No Provider factory or network toggle is exposed.

The existing allocation states remain `PLANNED` and `FINISHED`. A PLANNED row
must have `NOT_ASSESSED` integrity and null admission/infrastructure/primary/hash
fields. FINISHED rows require typed result fields, but those values are claims
to be checked against evidence, never the source of the denominator.

## Whole outgoing body

The dedicated schema allows only MOVE and WAIT with duration exactly 1. Exact
action/payload pairing is also enforced by the local validator; the root schema
does not use a union. General DecisionCandidate remains unchanged. Compatibility
with an actual service is unverified and is outside this offline gate.

The full body contains only model, fixed rendered input, `store=false`, the
strict schema format, and allowlisted reasoning/token parameters. The concrete
transport captures its received body, stored separately as `transport_body`;
the audit compares that capture against an independently reconstructed request.
Prompt bytes, canonical UTF-8 body and their SHA-256 hashes are recorded.

Tests intercept the actual transport call, compare it to stored captures, check
recursive semantic field boundaries and compare all 18 matched decision
opportunities across all 12 scripted trials. Clearing only `event_memory` in
detached copies must make complete bodies identical. At decision 1, memory is
also identical, so the full unmodified bodies must match.

No trial/allocation/block/order/condition/policy/k, run/trace/opportunity/attempt,
hidden clock/progress or researcher metadata is permitted in model input. The
length and content of the intended Event Memory remain visible by design.

The first-body golden SHA-256 for the explicitly selected M8-reference fixture is
`417326ce6d92dd10a7fecdbf02af8f36d337103f0f40bd5bfa33a4777bf3345b`.
It is stable because prompt v2, schema, model/parameters, initial semantic state
and canonical UTF-8 encoding are fixed. Source/runtime/IDs do not enter the body.
The golden complements structural and cross-condition assertions; it does not
replace them or establish a behavioral effect.

## Failure, denominator and replacement

Refusal, incomplete output, parser/schema failure, invalid actions, delivered
engine REJECTED/FAILED receipts and task incompletion are behavioral outcomes.
They remain in the denominator when their evidence is verified. Engine rejection
is not an internal engine exception. The empty-schedule Horizon fixture does
not naturally change routes mid-action; its FAILED test injects only completion
validation failure through the existing real kernel path.

Pre-response transport failure is infrastructure. A verified trial with no model
response and such a failure is outside the behavioral denominator, but its
allocation and all recorded attempts remain. If an earlier response was obtained,
even refusal or invalid JSON, a later transport failure does not remove that
verified responded trial from the denominator.

Observation acquisition and internal engine errors record sanitized
`observation_error`/`engine_error` journal entries. An internal fault that cannot
be reconstructed is not certified as a valid behavioral trial: integrity remains
EXCLUDED, while the independently hash-verified journal's infrastructure evidence
is retained as true. Corrupt/missing final snapshots do not erase that evidence.
An unverified record-only claim is separately labeled
`reported_infrastructure_failure`, not promoted to established evidence.

Recording failure stops execution. If storage cannot record the failure itself,
the remaining prefix establishes only the last durable operation. An intent
without a result is indeterminate; it is not an invented model failure or proof
that a request never reached its destination. Missing evidence uses null/unknown
infrastructure diagnostics, not false certainty.

`audit_cohort(..., batches=(...))` requires every original/replacement batch's
durable plan, journal, final snapshot and trial exports. It checks trial starting
plans, all-attempt inventory, order, seals and stored result claims. Denominator
counts come from fresh trial assessments. Rolling FINISHED back to PLANNED,
deleting trial evidence or injecting PLANNED outcomes fails closed. Record-only
cohort calls cannot certify an execution inventory. Planned replacements are
reported separately from finished attempts.

`add_replacement` still copies the cohort and appends a linked allocation with
the same condition/block, a new ID/order and null results. It never deletes or
overwrites the original and forbids duplicate replacement of the same allocation.
Its mechanical guard requires a FINISHED, VERIFIED infrastructure-failure result;
integrity-excluded/interrupted evidence cannot authorize a replacement merely by
setting a boolean. This is evidence validation, not authorization to execute live
replacements. Approval/maximum replacement policy remains pending; no automatic
replacement or implicit limit has been added.

## Crash and journal interpretation

Writers use exclusive creation, append ordered hash-chain rows, flush and fsync
each append. A sticky persistence failure prevents another decision. Existing
directories/files are never opened for overwrite by the runner.

`inspect_journal` returns the verified complete-line prefix and distinguishes
COMPLETE, TORN_TAIL, CORRUPT_RECORD and CHAIN_BREAK. Only an unterminated final
record can be a torn tail. Invalid complete middle/last records are corruption;
a complete but wrongly sealed row is a chain break. The strict `read_journal`
still rejects incomplete journals. No reader edits or truncates files.

`inspect_trial` reports the existing last journal kind, start/finish flags,
snapshot presence/validity/linkage and interruption independently. It covers:

- `trial_started`: no completed decision yet;
- `provider_intent`: response unknown;
- `provider_result`: response recorded, submission not yet established;
- `engine_intent`: canonical outcome unknown;
- `engine_receipt`: receipt recorded, decision snapshot may be absent;
- `trial_finished`: final digest recorded, `trial.json` may still be missing;
- valid or torn `trial.json`: checked independently of journal availability.

`inspect_cohort` reports which planned allocations have durable start/finish
entries and their trial prefixes. A started allocation is not rewritten into a
new allocation status; diagnostic journal kinds describe progress. Complete
trial/cohort auditing remains stricter than prefix diagnosis. No automatic
resume, exactly-once recovery, power-loss durability guarantee, or inference of
missing engine receipts is claimed.

## Execution freeze versus later audit

`validate_cohort(..., execution=True)` checks the frozen plan and current source
and runtime before creating output or making a mock call. The source identity
includes the working source digest and recorded Git metadata. The immutable
configuration/source/runtime plan is also bound by `frozen_plan_sha256`.

Later audits use `execution=False`: validate the recorded identity shape, plan
seal, configuration and cross-artifact/journal consistency without comparing
against today's checkout/Python. Compatible deterministic reconstruction still
checks semantic evidence; a future incompatible engine is not silently accepted.

Hashes are unkeyed integrity checks, not signatures. The implementation cannot
prove that claimed source bytes were executed if all external evidence is absent,
or detect a coordinated rewrite of every artifact and every retained anchor.
Preserve the original plan/journals/source externally when stronger provenance
is needed. No historical offline audit semantics were changed.

## Metrics and remaining human decisions

The eight existing metrics and `summarize()` are unchanged:
unique_destination_coverage, repeat_destination_count, task_completed,
decisions_to_completion, decision_attempt_count, engine_submission_count,
strict_no_repeat_success and post_completion_repeat_count.

Researcher-only derived metrics are independently recomputed:

```
pre_completion_repeat_count = repeat_destination_count - post_completion_repeat_count
primary_success = task_completed AND pre_completion_repeat_count == 0
```

The historical M8 configuration is an explicit fixture reference, not final live
approval. Final provider/model and drift acceptance, engine/allocation seeds,
reasoning/token/time/call/failure bounds, replacement approver/maximum and real
transport compatibility/review remain `REQUIRES HUMAN DECISION`. No actual API
use is allowed by this preflight. Mock correctness results are not behavioral
evidence. Validation outputs belong in new scratch directories, never inside
historical raw or the old development trial.
