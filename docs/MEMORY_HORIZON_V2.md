# Memory Horizon v2 — semantic Decision Context

This is the default offline protocol for new Distinct Places runs. It supersedes
only the model-visible input and WAIT/fixture behavior described below. The
[v1 specification and evidence](MEMORY_HORIZON_OFFLINE.md) remain historical v1;
no archived evidence is relabeled or stripped. This is not a live pilot, behavioral
result, Phase 2A closure, or formal Phase 2B entry/completion.

## Versions and scope

| Artifact | New default | Preserved historical identity |
|---|---|---|
| Prompt | `alderwick-memory-horizon-decision-2` | `alderwick-memory-horizon-decision-1` |
| Protocol | `alderwick-memory-horizon-offline-2` | `alderwick-memory-horizon-offline-1` |
| Audit | `memory-horizon-offline-correctness-2` | `memory-horizon-offline-correctness-1` |

Scenario `alderwick/memory-horizon-distinct-places-1`, raw Observation/EventTrace/
ActionRequest v1, export schema 3, engine, action handlers, and Recency selection
are unchanged. M8, Phase 1 and Recency prompt/input semantics are unchanged.
`PROFILE_V1` preserves the original text; `profile_for_protocol` dispatches the
runner and provider-free audit using the recorded protocol. Explicit historical
runs require both `PROFILE_V1` and `protocol_version=PROTOCOL_VERSION_V1`.
The CLI and default runner use v2. Mixed profile/protocol pairs fail before calls.

## Exact model-visible input

The following is the exact structural shape; angle-bracket values are semantic
data placeholders, and `event_memory` can be empty or contain several entries.

```text
{
  "observation": {"content": <current Observation.content>},
  "event_memory": [
    {
      "observation": {"content": <past Observation.content>},
      "action": {"action_type": <past action type>, "payload": <past action payload>},
      "receipt": {"status": <public status>, "reason_code": <public reason or null>}
    }
  ]
}
```

`adapters.memory_horizon_prompt.semantic_input` is a pure, deterministic, detached
projection of the already validated input selected by unchanged `event_memory_input`.
It preserves content, action intent, public outcome, oldest-to-newest ordering,
and selected trace count. The optional `PromptProfile.input_projection` runs only
for v2; no-profile, Phase 1, Recency and Horizon v1 rendering remain unchanged.
`PromptProfile.render` serializes the already prepared input.

The model receives no Observation envelope ID/run/actor/sequence/time/schema/digest,
trace/opportunity/attempt identifiers or counters, eligibility flag, open/close
times, ActionRequest authority/envelope IDs/submission time, receipt IDs or
start/resolve times. Condition names, policy IDs and k are absent. Stable semantic
entity, location, route, module and contributor identities remain in content.
Numeric semantic values such as traversal cost are retained; absence of numeric
time values cannot mean forbidding the same number in legitimate semantic content.

The unchanged scenario has empty Knowledge, and no `last_action`, `last_receipt`,
visit summary, completion flag or next-target field. Repeated Square current
inputs are identical canonical UTF-8 bytes, even at different raw ticks/sequences.
At a corresponding scripted decision all conditions have identical current input;
only the selected semantic history differs. After E1 Square→Inn, E2 Inn→Square,
E3 Square→Bakery, E4 Bakery→Square, memory is respectively `[]`, `[E4]`, `[E3,E4]`,
`[E2,E3,E4]`, with each E represented by the semantic shape above. Trace IDs are
research provenance only.

This projection is specific to the current Distinct Places composition. It does
not sanitize arbitrary future contributor content: adding Knowledge provenance,
clock-bearing content or receipt contributors requires a new boundary review.

## WAIT and horizon safety

Task semantics remain: visit Inn, Bakery and Well exactly once in any chosen
order, return to Square after each visit, then keep choosing WAIT. Only MOVE and
WAIT are allowed. The v2 prompt has no trial start/end tick or absolute-clock
instruction. WAIT must use integer `duration: 1`; the runner's protocol precheck
rejects other valid positive durations as `WAIT_DURATION` (an over-horizon request
retains `HORIZON_ACTION` precedence). It never clips or rewrites intent.

The researcher-side horizon remains tick 24. Fixed one-tick WAIT is safe at every
decision tick including 23. MOVE remains subject to the trusted duration precheck:
a cost-2 MOVE at tick 23 terminates with `HORIZON_ACTION`, without engine submission.
No remaining-time or rejection progress cue is delivered as another decision.
Core WAIT, parsing and historical protocols still accept their original durations.

Three normal target cycles finish at tick 12; twelve unit WAITs follow. Thus v2's
complete fixture has 18 decisions/submissions (six moves plus twelve waits),
where v1 had seven. Completion stays at decision 6. These counts must not be
retroactively applied to v1 evidence.

## Research preservation and audit

Raw `observations`, `event_traces`, action traces, requests, public receipts,
Events and replay evidence retain their full original provenance. This includes
all IDs/run/actor links, sequences, attempts, clocks, digests, schema versions,
eligibility and authority fields excluded from the model. Per-decision
`serialized_event_memory` and `event_memory_bytes` still describe the full raw
selected traces, alongside retrieved IDs/count; their historical meaning is intact.

V2 decisions additionally store:

- `model_visible_input`: the exact semantic input object.
- `model_visible_input_canonical`: its lossless canonical JSON string; encoding
  this string as UTF-8 gives the exact input bytes (not a pretty-printed export).
- `model_visible_input_bytes`: that UTF-8 byte length.
- Existing `provider_request.prompt` and `prompt_sha256`: exact text and hash.

Audit rebuilds a fresh application from raw initial world/schedule/seed, observes
and resubmits recorded intents, reconstructs the raw archive and selection, then
recomputes projection, canonical bytes, prompt and hash. It never creates a
Controller or invokes a Provider. It rejects resealed changes, including coherent
changes to projection, canonical string/size, prompt and hash. Raw archive and
metrics checks remain in force. V1 exports retain correctness-1 and their stored
inclusion comparison. The seal is not protection against an author fabricating a
wholly different internally consistent run. Wall-clock timeout checks retain
their recorded-evidence limitation.

## Offline fixture

`HorizonFakeProvider` now identifies as `memory-horizon-cursor-fixture-2`. Each
fresh instance has a deterministic harness cursor and the same destination tuple
for every condition. It reads only the current location and local exits, not
Event Memory, Research Archive, condition/k, simulation time or envelope IDs.
The cursor advances on generated valid MOVE intents, and exhausted plans use
unit WAIT. This fixture assumes successful execution of the scripted moves; it
is not receipt-aware retry/recovery behavior. Out-of-order calls no longer have
the old stateless semantics.

Success in every condition verifies protocol mechanics, not memory benefit or
model behavior. Even with the removed provenance cues, task difficulty, residual
semantic heuristics and memory necessity are not established. Memory list count
and order remain intentional experimental information. Any future live study
needs separate approval and a fixed statistical design.

Use the same CLI commands documented for v1 with fresh output directories, such
as `trials/horizon-v2-k3`; audit/replay dispatch from stored protocol automatically.
The runner rejects live Provider identities before calls, and the CLI has no live
flag. Trusted Python fixture identity is not a sandbox against malicious code.

## Verification

`tests/test_memory_horizon.py` covers recursive key/opaque-value exclusions,
independent exact-shape expectations, Square canonical byte equality, condition
equality, semantic E1–E4 windows, raw preservation/projection detachment, public
rejection semantics, provider-free export/replay/audit, resealed projection and
prompt tampering, fixed-WAIT enforcement, tick-23 safety and v1 compatibility.
The fake is also replayed against current-input-only prompts with all history
removed. Network sockets are forbidden in this suite.

Required gates: full pytest, Ruff check/format, strict mypy, and git diff --check.
Actual official historical M8 raw artifacts remain absent on this laptop; schema
1/2 fixture regressions do not replace that outstanding historical raw gate.

### 2026-09-17 completed gate

- Initial branch `memory-horizon-pilot-prep`, clean working tree, HEAD
  `49cc9f509308eb14034fa94179d37dad45a1e69f`; main
  `226a83c2effa1bb49b438509ca92f86ce1484a37`. HEAD is unchanged.
- Python 3.12.10; full pytest **1029 passed in 116.99s**, no warnings.
  This is 1007 existing cases plus **22 new cases in nine new test functions**.
  Six existing test functions (14 parameterized cases, including the renamed
  fake-provider test) were adapted to semantic input/cursor/unit-WAIT behavior.
  The Horizon suite now has 96 cases. Historical suites were not edited.
- Ruff check passed, Ruff format check passed (135 files), strict mypy passed
  (120 source files), and `git diff --check` passed.
- M8 prompt SHA-256 remains
  `95c5a4b5d8e1bca0e9f173e486f27b754ced0c017aa8f3c1aef885061bf2fa7d`.
  Phase 1 `event_memory_prompt({})` remains
  `980d67f933c38e5bc8970b9b0c1c28382ee914f9c55bc81bdab2e35d0b8e5815`.
  Horizon v1 instructions retain
  `b428f687dd9cf4faa54dba7436c023b9b2aa3d334f88d51df409fd71c166d7ed`.
- Existing `horizon-none/k1/k2/k3/repeat/incomplete` and
  `window-none/k1/k2/k3` exports all replayed to tick 24 and audited INCLUDED
  using their original audit versions. All **180 existing files** retained
  their before/after SHA-256 hashes. These are offline fixtures, not official
  historical model evidence.
- The full suite retains synthetic schema 1, generated schema 2, M8, Phase 1
  and Recency regressions. No dependency, network/API, live trial, historical
  raw edit, commit, push or PR was performed.
- Final working scope: 12 modified tracked files and one new documentation file.

### Changed files

| File | Change |
|---|---|
| [adapters/llm.py](../src/journeymap/adapters/llm.py) | Opt-in projection and separate exact-input recording |
| [adapters/prompt_profile.py](../src/journeymap/adapters/prompt_profile.py) | Optional projection callable, default absent |
| [adapters/memory_horizon_prompt.py](../src/journeymap/adapters/memory_horizon_prompt.py) | Preserved v1, semantic projection, v2 task text |
| [experiments/memory_horizon.py](../src/journeymap/experiments/memory_horizon.py) | Version dispatch, v2 default, unit-WAIT precheck |
| [experiments/memory_horizon_audit.py](../src/journeymap/experiments/memory_horizon_audit.py) | Versioned provider-free input/prompt reconstruction |
| [examples/memory_horizon.py](../src/journeymap/examples/memory_horizon.py) | Cursor fixture and v2 CLI default |
| [tests/test_memory_horizon.py](../tests/test_memory_horizon.py) | V2 invariants, tampering, compatibility |
| [API.md](API.md) | Projection/research contract |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Semantic boundary and provenance ownership |
| [ROADMAP.md](ROADMAP.md) | V2 preparation scope, unchanged milestone status |
| [TEST_STRATEGY.md](TEST_STRATEGY.md) | New gates and recorded results |
| [MEMORY_HORIZON_OFFLINE.md](MEMORY_HORIZON_OFFLINE.md) | Explicit historical-v1 label and v2 link |
| [MEMORY_HORIZON_V2.md](MEMORY_HORIZON_V2.md) | New v2 contract and verification report |
