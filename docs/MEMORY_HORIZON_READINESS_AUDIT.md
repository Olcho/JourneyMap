# Experiment 03 Memory Horizon — adversarial readiness audit

Audit date: 2026-09-19 (Asia/Seoul). **Executive verdict: NOT READY for an actual
LLM behavioral pilot.** Offline v2 correctness is supported; a live protocol and
the study's outcome/admission/allocation decisions are not frozen. No behavioral
effect of memory has been measured here.

## Baseline, method, and change decision

- Started implementation review without editing source. The initial checkout was
  `memory-horizon-pilot-prep` at `9fbd2cf`; local main and origin/main were stale.
  With the user's explicit Git-fetch-only permission, fetched origin/main,
  fast-forwarded main, and created `codex/memory-horizon-readiness-audit` from
  **`eaa4b4d5ea95c5c49f194aa9d4558de47020f922`**, PR #11's merge. Its tree is
  identical to the initially inspected hardening tree.
- Repository-wide inventory/read pass: all 138 tracked UTF-8 files; Python AST
  symbols/imports across source and tests. Detailed review followed the spec,
  architecture, API, ERD, test strategy, roadmap, historical protocols, and the
  full Horizon input/runner/archive/audit/engine/transport dependency path.
  Inactive social/resource modules were checked through dependency boundaries
  and their existing full regression suite, not treated as active Horizon inputs.
- Adversarial probes used in-memory fixture responses and injected local failures.
  Python processes removed `OPENAI_API_KEY` without reading or printing its value;
  socket connections were forbidden. `request_body()` was inspected/called as a
  pure function; no live transport was invoked. Git fetch was the only network
  operation. No installation, live trial, commit, push, or historical rewrite.
- This report is the first repository edit. **Production code changes: NONE.**
  No violation of the documented offline v2 behavior was demonstrated. Remaining
  blockers concern a new live experiment, not a reason to weaken the existing
  offline guard. Provider reuse below violates the documented fresh-instance
  composition requirement; it is an unenforced caller obligation, not evidence
  that the fresh-instance CLI leaks state. No refactor or protocol overwrite.
- Findings below give base-commit file/function/line evidence, a concrete trace,
  existing test coverage, and whether a change is needed. Paths are repository
  relative; line numbers refer to the base above, whose source is unchanged.

## Findings

### B1 — BLOCKER: no admitted live Horizon execution/audit protocol

**Evidence:** `src/journeymap/experiments/memory_horizon.py:122–147`, `run_trial`,
accepts only provider identity `kind=fixture,name=fake`;
`src/journeymap/experiments/memory_horizon_audit.py:69–110`, `_identity`, enforces
the same offline identity. `src/journeymap/examples/memory_horizon.py:73–109`,
`main`, constructs only a fake and exposes no live flag.

**Trace:** pass a controller with an honest live ProviderIdentity and PROFILE v2:
the runner raises before generate. Removing only this guard would leave exports
unadmittable by the recorded offline audit and would silently change the meaning
of `alderwick-memory-horizon-offline-2`. Relabeling live as fake is invalid.

**Tests:** `tests/test_memory_horizon.py:492`,
`test_new_runner_rejects_invalid_configuration_before_provider`, and `:527`,
`test_cli_has_no_live_flag`, catch the attempted bypass. This is intentional.

**Change needed:** a separately reviewed live protocol/entry point/audit identity
with explicit configuration and lifecycle gates; preserve offline v1/v2. See
the minimal design below. Do not implement live execution as an audit hotfix.

### B2 — BLOCKER: study-level outcome, allocation, and admission are not frozen

**Evidence:** `memory_horizon.py:73–119` computes eight metrics without selecting
a primary endpoint; `:283–321` records a single-trial manifest, not a cohort
allocation/order/sample-size plan. `memory_horizon_audit.py:409–420`,
`assess_trial`, explicitly certifies offline correctness, including valid failed
trials. `docs/MEMORY_HORIZON_V2.md:128–138` leaves statistical/live design open.

**Trace:** timeout + malformed output + six successful moves produces completion
at attempt 8, 20 total attempts, 18 submissions, and INCLUDED. Three receipt-bearing
rejected moves can also be INCLUDED without any completion. A coherent all-WAIT
trial reaches HORIZON and is INCLUDED with coverage zero. Selecting only completed
or INCLUDED cases as model successes changes the denominator after observing
outcomes. Different conditions can have different failure rates.

**Tests:** Horizon failure/bounds/metric tests correctly allow these records.
There is no test or artifact freezing a behavioral denominator, ordered cohort,
replication count, model drift rule, or primary comparison. This audit reproduced
the failed-attempt-then-complete trace and the all-WAIT trace.

**Change needed:** human choice and a versioned study plan before live; do not
change existing `INCLUDED`, `COMPLETED`, or metric meanings. Separate integrity,
protocol adherence, execution termination, and behavioral outcomes.

### F1 — VERIFIED: no researcher provenance in the current v2 model input

**Evidence:** `src/journeymap/adapters/memory_horizon_prompt.py:35–63`,
`semantic_input`, projects only Observation.content, action type/payload, and
public receipt status/reason. `:66–89` fixes identical task wording without
absolute start/end ticks. `src/journeymap/adapters/llm.py:302–358` renders and
records this projection, not the research decision envelope.
`src/journeymap/bootstrap.py:217–248` registers exactly self/position/local/records,
empty initial Knowledge, no runtime projection and no last receipt.

**Adversarial trace:** compare Square at raw tick 0/4/8/12 and after repeated
WAITs; inject distinctive condition/cycle strings into trial IDs. Raw envelope
simulation_time, sequence, observation/run/action/event IDs, timestamps, digests,
opportunity attempts and receipt times change, but the current rendered input
does not. None of those strings appears under an alternative model-visible key.
Progress summaries, completion flags and next targets are absent.

**Tests:** `test_v2_exact_payload_and_recursive_provenance_exclusion` at
`tests/test_memory_horizon.py:614`, `test_square_content_has_no_visit_information`
at `:190`, projection/raw detachment at `:715`, and resealed tampering at `:767`.
Additional audit probes used distinct opaque trial IDs across conditions.

**Change needed:** none for this composition. This is not a general-purpose
content sanitizer: a future Knowledge/time/receipt contributor would pass through
content unchanged and requires a new boundary review. Stable entity/route names
and traversal cost 2 are semantic input, not progress provenance.

### F2 — VERIFIED: cross-condition canonical rendered prompt equivalence

**Evidence:** `src/journeymap/adapters/prompt_profile.py:20–21`, `render`, uses
fixed instructions plus canonical JSON; `llm.py:313–355` changes only selected
event input for fixed profile/model/parameters. `openai_provider.py:19–27`,
`request_body`, has no trial/condition/header metadata or conversation history.

**Adversarial trace:** four fresh scripted trials with different opaque trial
IDs follow the same six MOVE + twelve WAIT stream. At each of 18 opportunities,
replace only `event_memory` with `[]` in a detached copy. The resulting **72 full
prompt byte strings**, and canonical pure request bodies, match by opportunity.
Instructions, JSON field presence/order, schema, model and reasoning settings
are identical. Full unmasked prompts correctly differ in selected history.

**Tests:** existing `test_four_conditions_share_inputs` at `:126`, exact rendered
prompt oracle at `:614`, and current canonical bytes at `:683`. Existing paired
fixtures use the same run ID; this audit additionally tested different IDs and
whole rendered strings/request bodies, not merely Python object equality.

**Change needed:** none. Equality is conditional on the same semantic state and
fixed configuration/action prefix. In a real rollout, actions cause states and
ticks to diverge; ordinal decision n across conditions is not automatically a
matched situation. A matched-history next-action study would need its own design.

### F3 — NON-BLOCKING: intentional memory length and semantic strategy cues remain

**Evidence:** `event_memory.py:121–122` selects the suffix without padding;
`memory_horizon_prompt.py:45–55` retains length/order and semantic route names;
`:68–74` names all three targets in a fixed textual order.

**Trace:** during warm-up, 0/1/2/3 entries reveal how much history is available;
the full k=3 suffix can reveal two target identities. k1's return receipt and
k2's outbound+return pair usually identify the same single latest target at
Square. Thus k1 versus k2 need not add a distinct visited destination. Target
wording/order and sorted exits can favor a fixed policy. These cues do not reveal
hidden progress outside the intended semantic history.

**Tests:** `test_actual_memory_horizon` at `:154` and semantic window oracle at
`:704` verify the exposed information, not that the task requires memory.
`test_fake_cursor_ignores_memory_and_condition` at `:431` explicitly succeeds
without using memory. No existing test establishes a behavioral advantage.

**Change needed:** none to v2. Interpret k as closed trace count, not number of
remembered destinations or hours. Padding, target-order counterbalancing or a
different task would be a new manipulation/prompt, not leakage cleanup.

### F4 — VERIFIED: exactly the most recent CLOSED eligible experiences

**Evidence:** `event_memory.py:79–95`, `EventMemoryContext`, validates actor/run,
eligibility, sequence, prior opportunity/Observation, and closure time;
`:173–251`, `EventTraceArchive.close`, admits bound public receipts once;
`llm.py:438–466`, `event_memory_input`, checks selected membership.
`bootstrap.py:217–248` excludes nested last_action/last_receipt entirely.

**Adversarial trace:** E1 Square→Inn, E2 Inn→Square, E3 Square→Bakery,
E4 Bakery→Square gives next retrieval `[]`, `[E4]`, `[E3,E4]`, `[E2,E3,E4]`.
E3's past Observation contains no E2 receipt/history. WAIT and engine REJECTED
are eligible closed experiences even when no ActorMoved Event exists. Current
or unclosed attempts cannot be selected. Archive size is not retrieval size.

**Tests:** Horizon tests at `:154`, `:704`; `tests/test_event_memory.py:191`,
`:227`, `:639`, `:665`, `:689`, `:712` cover scope, retry idempotency, rolling
windows, failures and rejection slots. All passed.

**Change needed:** none. Do not redefine eligibility as successful moves only.

### F5 — VERIFIED: provider/parser/refusal failures do not silently extend memory

**Evidence:** `llm.py:359–426` handles provider identity, duplicate responses,
refusal/incomplete and parsing; `memory_horizon.py:198–211` closes only an engine
submission with a delivered receipt; `:244–260` records failure and bounds.
`event_memory.py:142–171` retains opportunity lineage across unclosed attempts.

**Adversarial trace:** successful MOVE, timeout, malformed JSON, refusal, rejected
MOVE, WAIT. For k=0/1/2/3, retrieved counts are respectively
`[0,0,0,0,0,0]`, `[0,1,1,1,1,1]`, `[0,1,1,1,1,2]`, `[0,1,1,1,1,2]`.
With an enlarged explicit failure bound for this probe, three experiences close.
The timeout/parser/refusal and next decision receive exactly the same full prompt
within each condition; attempt/Observation counters do not leak. REJECTED closes
a real slot. Default three consecutive failures would stop before that recovery.

**Tests:** Horizon failure/precheck tests at `:295`, `:327`, `:826`;
Event Memory refusal/failure tests at `:136`, `:476`, `:665`, `:689`.
The mixed trace and full-prompt identity were additionally executed in this audit.

**Change needed:** none. A subsequent decision after failure is a fresh model
call, though there is no automatic transport retry/repair/fallback. Failure
frequency, call cap and runtime censoring still require a prespecified analysis.

### F6 — VERIFIED: submission errors preserve truth without inventing experience

**Evidence:** `application/turns.py:29–58`, `run_controller_turn`;
`application/session.py:329–358`, `_execute`; `core/kernel.py:238–255`,
`submit_action`; `memory_horizon.py:198–211,246–253`.
The audit reconstructs public receipts in `memory_horizon_audit.py:298–329`.

**Adversarial traces executed:** an exception on first engine entry produces
one attempt/submission, no arrival/trace, ENGINE_ERROR, integrity EXCLUDED.
A subscriber failure after the sixth successful MOVE commits the final return:
task_completed=true, decisions_to_completion=6, six attempts/submissions, but
only five Event Traces. The runner terminates, generates no next prompt, engine
replay is equal, and attempt/receipt audit excludes the interrupted run.

**Tests:** `tests/test_event_memory.py:376` tests receipt-bearing FAILED;
`:403` tests post-commit missing receipt; `tests/test_m7_idempotency.py` tests
cached retries and indeterminate execution. Horizon-specific injected engine
entry and final-return delivery failures were additional ad hoc probes.

**Change needed:** none. A delivered FAILED receipt is eligible; a thrown engine
error with no delivered receipt is not. Never infer a receipt from Research
truth. An engine-error trial can have a true task metric and still be excluded;
the behavioral protocol must decide how such trials are reported/replaced.

### F7 — VERIFIED: MOVE/WAIT/horizon rules are common; boundary censoring is real

**Evidence:** `memory_horizon.py:53–70`, `protocol_precheck`;
`experiments/alderwick.py:117–132`, `action_duration`;
`modules/movement/handlers.py:78–104`, `MoveHandler.prepare/validate_completion`;
`core/actions.py:24–57`, WAIT parser/handler. The fixture uses cost 2 on all routes.

**Trace:** unit WAIT at tick 23 completes at 24; valid cost-2 MOVE at 23 is
HORIZON_ACTION/no submit/no trace/no subsequent feedback prompt. WAIT 2 at 23
has HORIZON_ACTION precedence, whereas WAIT 2 at 0 is WAIT_DURATION. Invalid
route at 23 has precheck duration zero, reaches the engine and is REJECTED.
WAIT bool is rejected by parsing before the protocol comparison.

**Tests:** Horizon prechecks at `:327`, tick-23 MOVE at `:543`, WAIT enforcement
at `:796`, tick-23 WAIT at `:811`; authoritative movement duration and invalid
late route in `tests/test_pre_experiment_hardening.py`.

**Change needed:** none. Hidden remaining time means semantically identical
decisions near the horizon may be administratively censored. Compare this as a
fixed common protocol rule, not as proof of model memory failure. Increasing k
does not change duration; action mix can change opportunity counts and odd ticks.

### F8 — VERIFIED: the eight metrics match the specified committed-event semantics

**Evidence:** `memory_horizon.py:73–119`, `summarize`, joins committed stranger
ActorMoved.source_ref to the decision request ID. Square closes a pending target
cycle; WAIT and intentions alone do not count. Engine count sums actual
ActionTrace.engine_submitted, including entries that throw.

**Adversarial traces/results:** the matrix below was executed independently of
the cursor fixture using an explicit output sequence (except the reuse probe).
Rejected MOVE does not count as arrival; a provider/parser failure is an attempt
but not a submission; completion is monotonic while strict success is assessed
over the full recorded run. A post-commit failure still leaves its committed
arrival in the metrics, as F6 demonstrates.

**Tests:** `tests/test_memory_horizon.py:226–308` covers six target permutations,
pre/post-completion repeat, partial cycles and failures; `:395` reseals corrupt
values of all eight metrics and expects exclusion. Existing tests plus the
additional mixed traces found no arithmetic or completion bug.

**Change needed:** none. `task_completed` intentionally allows earlier repeats;
it is not equivalent to satisfying the entire exact-once instruction.
`strict_no_repeat_success` does not separately encode integrity inclusion.

### F9 — IMPORTANT BEFORE LIVE: post-completion WAIT changes outcome and cost

**Evidence:** `memory_horizon_prompt.py:72–73` instructs continued WAIT;
`memory_horizon.py:184–260` stops by horizon/bounds, never evaluator completion;
`:100–118` counts post-completion repeats and whole-run strict success.
`docs/MEMORY_HORIZON_V2.md:85–89` explicitly preserves completion at decision 6
but 18 total decisions instead of historical v1's 7.

**Trace:** six moves complete at tick 12. Twelve more model calls/WAIT receipts
follow under the successful fixture. After k consecutive WAITs, all retrieved
MOVE traces have left a k-sized window. A model may then resume moving: first
completion remains true, but post-completion repeats increase and strict success
becomes false. This tests sustaining a stop decision as well as visiting places.

For successful moves M and unit waits W ending at 24 without failures,
`2M + W = 24` and `decisions = M + W = 24 - M`. Therefore an extra return cycle
can reduce total decisions from 18 to 16 while worsening strict success. Total
decisions is not a completion-efficiency metric. The successful path makes three
times the six completion calls; actual cost is not necessarily three times
because input length, reasoning/output usage and latency vary. Larger windows
also increase input size. Existing 300-second wall budget allows about 16.7
seconds per call for an 18-call path before overhead, not a guaranteed budget.

**Tests:** `test_six_permutations_complete_then_wait` at `:226`, repeat metrics
at `:254`, rolling windows in Event Memory tests. Tests verify mechanics, not
clinical/statistical suitability or actual token cost.

**Change needed:** a human choice of primary outcome and cost/failure reporting.
Keep v2 unchanged. Early-stop removes twelve decisions and makes later-repeat
outcomes unobservable, uses researcher truth to set stopping, and changes the
probability of full-run strict success. It needs a new protocol and analysis
version, even if no explicit completion flag is sent to the model.

### F10 — IMPORTANT BEFORE LIVE: fresh provider lifecycle is a caller obligation

**Evidence:** `memory_horizon.py:135–147` checks controller.last_decision and
event_traces, not provider history. `llm.py:196–215` stores the supplied provider
reference and creates fresh archive/response-ID state. `examples/memory_horizon.py:20–62`
keeps `_cursor` inside the fake; `:101–107` correctly creates a fresh instance
per CLI trial. `docs/MEMORY_HORIZON_V2.md:120–126` documents that requirement.

**Executed counterexample:** `p = HorizonFakeProvider()`; run a fresh Controller(p)
for trial A, then a different fresh Controller(p) for trial B. A completes with
18 decisions; B has 24 WAITs, coverage 0, completion false. Both get INCLUDED.
A third Controller with a fresh provider restores completion at 6/18. Fresh
controller alone is not proof of trial independence.

**Tests:** `test_fresh_controller_required` at `tests/test_memory_horizon.py:588`
catches controller reuse only. Fresh four-condition fixtures pass. No existing
test rejects a reused provider or certifies a future stateful provider factory.

**Change needed:** the future live/batch composition must own per-trial provider,
controller, monitor and application creation (or prove a transport truly
stateless), and reject/reinitialize conversation/cache/session state. Do not
reset a stateful fake on every generate or expose a run ID in the semantic prompt
to fix a caller misuse. No offline contract change is required in this audit.

### F11 — VERIFIED: fresh application/archive/cache scopes do not leak

**Evidence:** `memory_horizon.py:152–158` creates a new in-memory kernel/application;
`application/session.py:65–96` creates new Observation history, traces and request
cache; `event_memory.py:145–155` rejects cross-run/actor continuation. Default
BudgetMonitor is new at `memory_horizon.py:149`. Provider body at
`openai_provider.py:19–27` has no previous_response_id, conversation or tools.

**Trace:** fresh trials can use the same trial ID in separate directories and
obtain the same engine inputs without retrieving past-run memory or cached
receipts. Reusing the Controller is rejected. Reusing a registered monitor
raises duplicate-contributor error rather than silently carrying its attempts.
Run IDs are `trial_id + ':run'`: uniqueness across a cohort is a caller duty.

**Tests:** same-ID fresh runs at Horizon `:126`, controller reuse at `:588`,
Event Memory scope tests at `:210`, M7 cache/run isolation tests. The mixed-ID
canonical prompt probe also verifies IDs are research-only.

**Change needed:** no engine/cache change. Freeze unique cohort trial IDs and
fresh object ownership in the live composition. This result does not cancel F10.

### F12 — IMPORTANT BEFORE LIVE: wire schema and provenance scope need a decision

**Evidence:** `adapters/decision_schema.py:9–74`, `decision_schema`, admits eight
actions and positive WAIT durations; `openai_provider.py:19–27` always sends it.
The Horizon prompt advertises only MOVE/WAIT duration 1. `llm.py:275–298,366–382`
records schema/raw_output and allowlisted metadata. `openai_provider.py:56–82`
extracts message text and metadata, not the full provider response document.

**Trace:** a schema-valid REST or WAIT duration 2 reaches the Horizon precheck
and terminates; a schema constrained to MOVE/unit-WAIT could suppress these
failures. This changes model-visible constraints although no condition identifier
is exposed. On refusal, extracted text and a refusal flag are stored, but the
entire HTTP response/output-item structure is not. Do not call that a byte-exact
archive of the complete response body.

**Tests:** schema/transport are mocked in `tests/test_m8_llm.py` and
`tests/test_m8_revision.py`; Horizon `:327`, `:796` check protocol rejection.
No live service capability or exact Horizon transport schema has been validated.

**Change needed:** choose explicitly whether to reuse the historical broad schema
or introduce a separately named Horizon wire schema. Preserve old schema IDs.
Choose whether extracted raw text plus metadata is sufficient, or add a sanitized
versioned response envelope. Never archive credentials/headers/error bodies to
obtain more provenance. This audit makes no external model-support assertion.

### F13 — VERIFIED: authority and historical replay boundaries remain intact

**Evidence:** `llm.py:131–154` binds intent to trusted Observation authority;
`application/turns.py:29–58` validates before GamePort submit;
`core/kernel.py:191–255,309–356` routes actor handlers and deterministic commit;
system events use a separate dispatch at `:280–307`. `core/replay.py:34–70`
uses recorded actions, not a Provider. `experiments/alderwick.py:543–577`
exports only to a new directory. Version dispatch is explicit in
`memory_horizon.py:34–39` and `memory_horizon_audit.py:401–406`.

**Trace:** injected authority fields fail candidate parsing; a rejected intent
does not create an arrival; a committed world result without public receipt does
not enter Event Memory. A v1 export retains its original prompt and audit version.
Existing official M8 raw on this machine replays to tick 24 but schema 1 remains
EXCLUDED under strict audit with NEW_PROVENANCE_REQUIRED; it is not upgraded.

**Tests:** architecture/authority/determinism/atomicity suite; Horizon `:840`
v1 replay/hash preservation; `test_pre_experiment_hardening.py:379` exercises
the actual local official artifact when present, in addition to synthetic data.
This audit separately invoked provider-free replay/audit of that official raw.

**Change needed:** none. All 262 pre-existing non-cache trial files retained their
SHA-256 hashes after the audit. Older documents' statements that official raw was
absent describe their historical machines; they are not rewritten here. Availability
and byte preservation do not retroactively satisfy newer inclusion requirements.

## Executed adversarial metric matrix

All rows are **offline correctness probes**, not behavioral evidence. C means the
six successful moves Inn→Square, Bakery→Square, Well→Square, each preceded by the
appropriate Square outbound MOVE. Unless capped or interrupted, unit WAIT fills
the horizon. `pre-repeat` inserts an extra Inn cycle before completing C;
`post-repeat` adds it afterward. Columns correspond exactly to the eight metrics.

| Trace | Unique coverage | Repeat count | Completed | Decisions to completion | Attempts | Engine submissions | Strict success | Post-completion repeats |
|---|---:|---:|---|---:|---:|---:|---|---:|
| C + WAIT to tick 24 | 3 | 0 | true | 6 | 18 | 18 | true | 0 |
| Pre-repeat + remaining cycles | 3 | 1 | true | 8 | 16 | 16 | false | 0 |
| C + post-repeat | 3 | 1 | true | 6 | 16 | 16 | false | 1 |
| Stop on third target, max_decisions=5 | 3 | 0 | false | null | 5 | 5 | false | 0 |
| Third target then WAIT, max_decisions=6 | 3 | 0 | false | null | 6 | 6 | false | 0 |
| Rejected absent route + C | 3 | 0 | true | 7 | 19 | 19 | true | 0 |
| Timeout + malformed JSON + C | 3 | 0 | true | 8 | 20 | 18 | true | 0 |
| Refusal + C | 3 | 0 | true | 7 | 19 | 18 | true | 0 |
| WAIT only | 0 | 0 | false | null | 24 | 24 | false | 0 |
| Exception on first engine entry | 0 | 0 | false | null | 1 | 1 | false | 0 |
| Commit final return, fail receipt delivery | 3 | 0 | true | 6 | 6 | 6 | true | 0 |

The last two rows are integrity EXCLUDED and terminate ENGINE_ERROR; the other
rows are INCLUDED under offline correctness, including the capped partial cycles.
Engine submission means the application entered the engine call, not success.

## Human decisions required before live

1. Select a primary endpoint and estimand. Whole-horizon strict_no_repeat_success
   measures both traversal and continued abstention; task_completed measures
   eventual coverage of cycles even after earlier repeats. Use
   decisions_to_completion with explicit handling of null/censored cases, not
   total decision_attempt_count as a speed score. Keep the current WAIT tail unless
   a separately versioned early-stop study is explicitly chosen.
2. Choose independent trial count, allocation/order, seed schedule and stopping
   rule before observing results. Counterbalance/randomize condition order in a
   researcher-only cohort manifest. The existing deterministic seed 42 controls
   the engine, not LLM sampling or API determinism.
3. Fix model/provider identity policy, reasoning, token cap, transport timeout,
   trial wall budget, failure bounds and retry policy identically across conditions.
   Longer prompts/latencies can interact with the fixed wall budget. Record all
   attempted allocations, failures and any replacement linkage.
4. Freeze behavioral inclusion/exclusion independently of offline integrity.
   Prespecify refusal/invalid-output/transport/engine-error/horizon handling and
   model drift. Do not discard task failures because they are inconvenient.
5. Decide broad versus Horizon-only wire schema and response archival scope (F12).
   Frozen prompt wording/order is part of the manipulation; changing it later
   requires new identity/evidence. No behavioral result is inferred from fixtures.

## Minimal future live design — proposal only, not implemented

- Introduce an explicit identity such as `alderwick-memory-horizon-live-1` and a
  corresponding live audit version. Keep offline v1/v2 dispatch and live rejection
  intact. A separate, narrow composition entry point owns a fresh controller,
  provider, monitor, kernel/application and unique research trial ID per trial.
  It must not inject researcher/progress data into model input or reuse a remote
  conversation, previous response, summary or agent cache.
- Reuse semantic projection, unchanged closed-trace selection, GamePort authority,
  handlers and replay inputs. PROFILE v2 may be reused only if its rendered bytes
  and WAIT semantics are unchanged. A changed prompt or constrained schema receives
  a new version. Review the **whole outgoing body**, not only INPUT_JSON, using a
  recording mock transport in all four conditions before enabling live transport.
- Freeze a cohort manifest outside model input: intended allocations/order,
  repetition/seed schedule, model policy, endpoints, bounds, exclusion/replacement
  rules, source commit/hash/runtime, and all relevant artifact hashes. Require
  prepared output locations and preserve partial/failed attempt records. Existing
  export occurs after the run; it is not crash-safe live capture.
- Define a live evidence schema if adding cohort linkage, transport-body/response
  envelopes or per-call persistence. Do not silently add new meaning to offline
  schema 3. Keep raw Observation/EventTrace/ActionRequest v1 if their shape is
  unchanged. Separate factual record validation from study eligibility; interrupted
  engine replay limitations must remain visible.
- Implement/test only after these choices: live-identity mock transport, schema
  and prompt golden bodies, shared-provider reuse prevention, timeout/refusal/
  incomplete/parser/rejection/engine-error matrices, all eight metric oracles,
  cross-condition equivalence, provider-free replay and preserved historical bytes.
  No Selective Memory, vector DB, reflection, RL or new world feature is needed.

## Freeze inventory extracted from code

| Item | Current source/record | Required live freeze |
|---|---|---|
| Provider/model identity | `provider.py:59–149`; runner manifest identity/model; per-decision returned model | Requested model and returned identity/snapshot acceptance, adapter implementation/version, endpoint and timeout; alias drift policy. No particular live model availability was checked. |
| Prompt | `memory_horizon_prompt.py:66`; `llm.py:328–358` full text/hash | v2 exact instructions, projection, canonical JSON, per-call prompt/body bytes/hash; new ID for any wording change. |
| Protocol | `memory_horizon.py:29–31,53–70,184–260` | New live identity; scenario, tick 0–24, MOVE cost, unit WAIT, precedence, failure/call/wall bounds, completion-tail rule. |
| Schemas | `decision_schema.py:9`; runner manifest schema 3; `event_memory.py:18`; Observation/ActionRequest v1 | Exact DecisionCandidate schema and transport response format, export/audit versions, current raw schema versions; explicit new IDs if changed. |
| Reasoning | `llm.py:98–113,198–203` | Explicit common effort. Current default medium is a default, not study approval. |
| Output token cap | Same parameter allowlist/default | Explicit common cap; current default 4096. Retain incomplete status and usage; do not silently retry with a larger cap. |
| Seed | `memory_horizon.py:126,153`; engine_manifest.seed | Engine seed and separately cohort allocation seed/order. No provider sampling seed exists in the current allowlist. |
| Condition | `memory_horizon.py:42–50,301–302`; decision memory provenance | NoMemory or exact recency k1/k2/k3 policy/version; all other configuration held fixed; never put condition labels into input. |
| Trial order | Single trial API only; ID at `:152` | External immutable ordered allocation/cohort ID, repetitions, unique trial/run IDs, execution times, replacement linkage. Currently absent. |
| Inclusion/exclusion | `memory_horizon_audit.py:409`; `TrialPolicy` in `alderwick.py:65–81` | Separate live integrity and behavioral criteria, all-attempt denominator, invalid/refusal/timeout/engine-error and drift policy. |
| Raw response/provenance | `llm.py:275–298,357–426`; `openai_provider.py:56–82` | Exact prompt/body, extracted raw output, parsed intent, requested/returned model, response ID/status/refusal/incomplete/usage, latency, failure type, action linkage. Decide full sanitized envelope scope; current raw_output is not the full HTTP document. |
| Replay inputs | `memory_horizon.py:262–279,339–352`; `core/replay.py:34–70` | Initial state/Knowledge and digests, manifest/module versions/seed, empty versioned schedule, submitted requests and advance_to, full report; observation/decision/trace archives for prompt reconstruction. Replay reproduces engine outcomes, not model sampling. |
| Software/runtime | `experiments/provenance.py:32–74`; `memory_horizon.py:290–292` | Clean committed source, source scope/hash algorithm, working_source_sha256, Python/dependency versions and configuration. Recording a hash alone is not enforcing a study freeze. |

## Validation and artifacts

- Python **3.12.14**, bundled local runtime with existing `.venv/Lib/site-packages`;
  the repository venv launcher pointed to a missing Python executable. No packages
  or dependencies were installed/changed.
- Full pytest with socket connections denied and API key removed from the child
  environment: **1029 passed in 59.19s**, no skips/warnings reported.
- `ruff check .`: passed. `ruff format --check .`: **135 files already formatted**.
- `mypy --strict`: passed, **120 source files** (configured scope includes tests).
- Additional in-memory adversarial traces: metric matrix, four-condition failure
  windows, provider reuse, engine-entry/post-commit failures, and 72 canonical
  full-prompt/request-body comparisons. These are audit probes, not additional
  committed pytest cases and not LLM trials.
- Actual `trials/m8-first-official-sol` replay: equality at tick 24. Strict audit
  remains EXCLUDED / NEW_PROVENANCE_REQUIRED for schema 1. No raw regeneration.
- New ignored local audit scratch directory:
  `trials/readiness-audit-20260919/` (SHA-256 ledger, pytest temp/cache, mypy cache).
  The 262-file preservation ledger covers all pre-existing non-pytest/non-mypy
  trial directories, including the actual official raw and corrupted fixtures.
- `git diff --check`: passed; the new untracked report was additionally checked
  with `git diff --no-index --check -- NUL docs/MEMORY_HORIZON_READINESS_AUDIT.md`.
  Git emitted only its LF-to-CRLF conversion notice. Final preservation comparison:
  **262 checked, zero changed/missing files**.
- Exact repository change: **`docs/MEMORY_HORIZON_READINESS_AUDIT.md` (new)**.
  Production code, committed tests, historical protocol documents and trial raw
  are unchanged. No source fix means no new regression test was needed; the
  additional adversarial probes are distinguished from the 1029 existing tests.

Suggested next step: resolve and freeze the five human decisions above, then
prepare the separate live protocol and mock-transport readiness gates. Actual
LLM execution remains a later, explicitly authorized task.
