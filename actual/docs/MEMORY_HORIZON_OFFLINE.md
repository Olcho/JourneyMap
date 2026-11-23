# Memory Horizon / Distinct Places — offline preparation

**Historical v1 specification and evidence.** New runs default to the
[v2 semantic Decision Context protocol](MEMORY_HORIZON_V2.md). The descriptions,
clock fixture, counts and verification results below retain their original v1
meaning; existing raw exports are not changed or relabeled.

This branch prepares a controlled behavioral pilot. It does **not** declare
Phase 2A closed, or Phase 2B formally entered/completed. Phase 2A historical raw
artifact gate and Recency Window PR/merge remain separate outstanding work.
Reference: [Shared Research Objective](https://app.notion.com/p/3dce2656dc268188bbc2ec093050b0ba),
Phase 2B and its Distinct Places example, read 2026-09-15.

## Versioned scope

| Artifact | Identity |
|---|---|
| Scenario | `alderwick/memory-horizon-distinct-places-1` |
| Prompt | `alderwick-memory-horizon-decision-1` |
| Protocol | `alderwick-memory-horizon-offline-1` |
| Audit | `memory-horizon-offline-correctness-1` |
| Export | schema **3**, unchanged inventory |
| Event Trace / Observation / ActionRequest | v1, unchanged |

Conditions: `NoMemory()` in Event Memory mode, `RecencyEventMemory(1)`, `(2)`, `(3)`.
All use the same prompt profile, initial world/Knowledge, model configuration,
schedule, action contract, observation contract and evaluator. Only retrieval
policy differs. IDs distinguish runs; paired offline correctness fixtures also
use identical run IDs to compare engine and Observation artifacts directly.

## World and task

The new author input calls `fixture.initial_world()` to reuse **all** existing
locations and routes (including non-target exits), preserving directed route IDs,
passability and MOVE cost 2. Only stranger's initial position changes from West
Gate to Village Square in this new snapshot. Existing NPC entities stay stationary;
their turns never run. The intact bridge fixture value is inert and is not perceived.

Only movement/knowledge modules and MOVE/WAIT handlers are composed. No social,
trade or survival modules, bridge handler, autonomous NPCs or scheduled events
are installed. Initial Knowledge is empty and has no runtime projector.
The generic engine, deterministic mutation boundary and historical compositions
are unchanged.

The task is fixed prompt text: visit Inn, Bakery and Well exactly once, returning
to Square after each; choose the order independently; when all cycles seem
complete, WAIT through the remaining horizon. The engine never recommends a
destination, enforces a visit order, exposes progress, or stops on task completion.

Tick 0–24 remains fixed. Existing movement timing determines the horizon precheck;
an over-horizon request is refused before submission, never clipped. Start-invalid
MOVE reaches the engine and its REJECTED receipt may form a closed trace. Unsupported
actions are a protocol pre-submit termination. Failure/call/decision/wall bounds
remain explicit; unsuccessful termination is not COMPLETED/HORIZON. WAIT advances
time and creates an action receipt/closed experience, but no domain Event.

## Prompt and perception boundary

`LLMController(..., event_memory=..., prompt_profile=PROFILE)` is the sole opt-in.
`PromptProfile` is frozen version/instructions data with a deterministic JSON
renderer. It requires Event Memory mode. No profile keeps the exact historical
M8 and Phase 1 prompt text/version/rendering. The historical runner rejects
explicit profiles before Provider calls, preventing accidental protocol relabeling.

Current Observation contains only self, position, local exits and Knowledge.
This composition explicitly sets `include_last_receipt=False` and never registers
`last_receipt`. Existing M8 retains its own setting and contributor.

This exclusion matters: if E3's Observation contained E2's `last_action`, k=2
could recover the Inn return one experience beyond the intended window. New
traces retain their full original Observation, own ActionRequest and public
receipt, with no retrospective stripping. `visited_places`, completion flags,
next target or other researcher metrics are never projected into Knowledge,
Observation or prompt. Square content is identical across repeated returns;
normal envelope IDs, sequence and simulation_time remain visible.

After E1 Square→Inn, E2 Inn→Square, E3 Square→Bakery, E4 Bakery→Square:

| Condition | At the next Square decision |
|---|---|
| No Event Memory | `[]` |
| k=1 | `[E4]` |
| k=2 | `[E3,E4]` — no nested E2 last_action |
| k=3 | `[E2,E3,E4]` — Inn return in E2's own request |

First-decision empty memory, same actor/run, past closure order and self-memory
exclusion remain enforced by the unchanged archive/input contracts. Provider/parser
failures do not consume a memory slot. Receipt-bearing engine rejections do.
All four conditions still archive closed experiences, including No Event Memory.

## Research-only metrics

Metrics are computed after the run from committed stranger `ActorMoved` events
and decision/action traces. `Event.source_ref` joins to the request ID;
`causation_id` identifies a transition, not a request. Failed intents and repeated
reads do not count as arrivals. A successful return to Square closes a pending
target visit; WAIT at the target does not.

| Metric | Definition |
|---|---|
| `unique_destination_coverage` | Number of distinct successfully reached targets, 0–3 |
| `repeat_destination_count` | Sum of each target's successful arrivals after its first; Square excluded |
| `task_completed` | All three target visit/return cycles completed at least once, regardless of later repeats |
| `decisions_to_completion` | First completion's decision sequence, including earlier failed attempts; otherwise null |
| `decision_attempt_count` | All decision attempts, including provider/parser/Observation failures |
| `engine_submission_count` | Actual engine submissions from application traces |
| `strict_no_repeat_success` | `task_completed` and zero repeat arrivals over the full recorded run |
| `post_completion_repeat_count` | Repeat target arrivals after first task completion |

Example: three cycles then WAIT yields coverage 3, completion at decision 6,
7 attempts/submissions and strict success. Three cycles then another Inn cycle
yields completion at 6, one repeat/post-completion repeat, strict success false.
Stopping at the third target before returning yields coverage 3, completion false,
completion decision null. Execution status and task completion are independent.

## Offline Provider and commands

`HorizonFakeProvider` is a frozen, stateless **clock-scripted test fixture**.
It reads the delivered current location, simulation time and exits. Its immutable
destination tuple defines the test case; it uses visible tick/4 as the script slot,
not a hidden call counter, visited set or researcher archive. It returns via a
delivered local exit, then waits when the script is exhausted. Calling it out of
order or repeatedly with the same input gives the same response.

It intentionally ignores retrieved memory when choosing actions. The same script
therefore succeeds under all four conditions; it proves execution, delivery and
evaluation correctness, **not** a benefit of memory or any model performance.
Retrieval differences are asserted against the actual prompts independently.
Public clocks/IDs remain potential behavioral cues for a future model; no claim
that solving this task logically requires memory is made.

The new runner rejects live Provider identities before any call. The CLI has no
live option, credential lookup or transport import. Provider identity is a trusted
Python composition contract, not a sandbox against a malicious fake implementation.

```powershell
python -m journeymap.examples.memory_horizon --memory no-memory --trial-id horizon-offline --output trials/horizon-none
python -m journeymap.examples.memory_horizon --memory recency-k1 --trial-id horizon-offline --output trials/horizon-k1
python -m journeymap.examples.memory_horizon --memory recency-k2 --trial-id horizon-offline --output trials/horizon-k2
python -m journeymap.examples.memory_horizon --memory recency-k3 --trial-id horizon-offline --output trials/horizon-k3
python -m journeymap.examples.memory_horizon --memory recency-k3 --fixture repeat --trial-id horizon-repeat --output trials/horizon-repeat
python -m journeymap.examples.memory_horizon --memory recency-k3 --fixture incomplete --trial-id horizon-incomplete --output trials/horizon-incomplete
python -m journeymap.examples.memory_horizon --audit trials/horizon-k3
python -m journeymap.examples.memory_horizon --replay trials/horizon-k3
```

Output directories must be new. Existing exports are never overwritten.
The four paired examples use the same neutral trial ID in independent directories
to avoid exposing the condition name through Observation's normal run ID.

## Replay and independent audit

Use `experiments.memory_horizon_audit`; historical readers are not broadened.
The existing schema 3 inventory reader/writer and content seal are reused.
Replay reconstructs this exact scenario, empty schedule and initial world, then
uses the existing ActionRequest-only ReplayHarness. It compares Events/results,
final state/digest and the complete engine report.

Audit additionally reconstructs each delivered Observation through a fresh
application, parses recorded raw candidates, resubmits recorded actor intents,
matches public receipts and full application traces, rebuilds archive opportunities
and closures, and recomputes policy selection, IDs/count/serialized bytes, prompt
text/hash/config, activation inventory and metrics. No Controller or Provider is
created during replay/audit. Recomputed inclusion is compared to stored inclusion.

`INCLUDED` means correctness within this offline protocol, including valid failed
trials. It is not model-performance admission. Wall-time deadlines cannot be
deterministically rerun; audit checks recorded elapsed-time evidence and failure
boundaries. Unreconstructable interrupted engine calls are excluded; resume or
recovery guarantees are not added. The seal detects inconsistency, not a malicious
author constructing and resealing an entirely different internally consistent run.

## Gates and remaining work

`test_memory_horizon.py` covers four-condition equality, Square content, actual
memory horizons, nested receipt exclusion, all six target permutations, repeats,
incompletion/missing return, post-completion behavior, failure lineage/counts,
bounds, prompt defaults, forbidden configurations and resealed tampering.
Export/replay/audit tests forbid Provider/Controller construction and check raw
export bytes are unchanged. The existing Phase 1 and Recency Window tests retain
their actor/run/time and FAILED receipt coverage; no old test is weakened.

Required gate: full pytest, Ruff check/format, strict mypy, git diff --check.
Original historical official raw artifacts are absent on this laptop. Existing
synthetic schema 1 and generated schema 2 regression evidence does not substitute
for the outstanding historical raw gate. No API calls, commits, pushes or PRs
are part of this preparation. A future live pilot still needs its own fixed model,
repetition, failure/exclusion and statistical interpretation design, after the
remaining Phase 2A delivery gates.

## 2026-09-15 verification results

- Base/HEAD: `399898381b182f3014dd0a5111aca42d20052241`.
- Branch: `memory-horizon-pilot-prep`; no commit, push or PR.
- Python 3.12.10, existing `.venv` runtime; no dependency changes.
- **74 new tests passed**; full suite **1007 passed (933 existing + 74 new)**,
  97.97 seconds. No warnings in the final full run. Fresh test temp/cache paths
  avoided the old cache ACL issue encountered during the first focused run.
- Ruff check passed; format check **134 files already formatted**; strict mypy
  passed for **120 source files** (the configured mypy scope includes tests).
- Final formatting only normalized the inserted historical runner guard's EOL;
  its behavior and versioned prompt bytes did not change.
- Historical M8 `PROMPT` SHA-256 remains
  `95c5a4b5d8e1bca0e9f173e486f27b754ced0c017aa8f3c1aef885061bf2fa7d`.
  Phase 1 `event_memory_prompt({})` SHA-256 remains
  `980d67f933c38e5bc8970b9b0c1c28382ee914f9c55bc81bdab2e35d0b8e5815`.
- Existing schema 1 synthetic fixture, schema 2 generated fixture, M8, Phase 1
  and Recency Window regressions passed unchanged. Schema 1 retains
  `NEW_PROVENANCE_REQUIRED` in strict audit.
- Pre-existing `trials/window-none`, `window-k1`, `window-k2`, `window-k3`
  each replayed to tick 24 and audited INCLUDED; every file's SHA-256 remained
  unchanged. These are offline artifacts, not official historical raw evidence.
- `trials/m8-first-official-sol` is absent; actual historical raw compatibility
  is still unverified on this laptop.

Final independent exports (same neutral `horizon-offline` trial ID):

| Directory under `trials/` | Coverage | Repeats / post-completion repeats | Completed | First completion decision | Attempts / submissions | Strict success |
|---|---:|---|---|---:|---|---|
| `horizon-none` | 3 | 0 / 0 | true | 6 | 7 / 7 | true |
| `horizon-k1` | 3 | 0 / 0 | true | 6 | 7 / 7 | true |
| `horizon-k2` | 3 | 0 / 0 | true | 6 | 7 / 7 | true |
| `horizon-k3` | 3 | 0 / 0 | true | 6 | 7 / 7 | true |
| `horizon-repeat` | 3 | 1 / 1 | true | 6 | 9 / 9 | false |
| `horizon-incomplete` | 2 | 0 / 0 | false | null | 5 / 5 | false |

All six ended COMPLETED/HORIZON at tick 24, replayed equally, audited INCLUDED,
and matched stored inclusion. All files retained their hashes across replay/audit.
They are git-ignored local evidence. `horizon-dev` is an earlier development
fixture and is not the final evidence set.

### Changed files and review scope

Existing files: `bootstrap.py` adds separate factories; `adapters/llm.py` adds
the optional frozen profile; `experiments/alderwick.py` adds only the explicit
profile rejection guard. API/ROADMAP/TEST_STRATEGY gain additive documentation.

New files: `adapters/prompt_profile.py`, `adapters/memory_horizon_prompt.py`,
`scenarios/alderwick/memory_horizon.py`, `experiments/memory_horizon.py`,
`experiments/memory_horizon_audit.py`, `examples/memory_horizon.py`,
`tests/test_memory_horizon.py`, and this protocol document.

No changes to existing fixture, M8 scenario schedule, Event Memory policy/archive,
Core, module handlers, historical inclusion/replay rules, or existing tests.

**Pilot-design readiness: Yes**, for isolating and checking the delivered-memory
manipulation: equal current inputs, no nested last_action or visit summary,
exact k-specific retrieval and independent outcome reconstruction are verified.
This does not establish a behavioral effect or prove memory is necessary to
solve the task. Visible time/IDs and a model's own fixed-order heuristics remain
possible strategies. Live execution, statistical conclusions, formal Phase 2B
status and the outstanding Phase 2A historical/merge gates are not included.
