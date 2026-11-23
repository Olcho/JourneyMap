# MemoryReplica v1: 120-world evaluation results (2026-11-23)

> **Research-result record, not a code release.** This document summarizes the completed evaluation reported on 2026-11-23 and reviewed on 2026-11-24. The MemoryReplica runner, frozen inputs, raw Responses, SQLite journal and evaluation scripts have **not** been verified as present in this repository's `main` branch. Do not use this document alone as a reproducibility claim or assume that checking out `main` runs this experiment.

## Research question

In a partially observable simulated village, how does the policy used to select roads from past successful or unsuccessful missions affect (1) reconstructing the complete map and (2) subsequent navigation to a new goal?

For each world, all five conditions used the same environment, starting state, goal and generated mission history. The four memory-present conditions each received four distinct historical road records after excluding duplicated or already visible records.

| Code | Selection rule | Historical roads supplied |
| --- | --- | ---: |
| N | No prior road memory | 0 |
| S | Most recent roads from successful missions | 4 |
| F | Most recent roads from failed missions | 4 |
| M | Two roads from success and two from failure | 4 |
| R | Four most recent roads regardless of mission outcome | 4 |

These past missions are controlled, generated histories. They are **not** evidence of an autonomous long-running agent learning from its own live history.

## Protocol and evidence scope

- First evaluation: 60 generated worlds, each run under five policies (300 runs).
- Follow-up evaluation after inspecting the first results: 60 new worlds and 300 further runs.
- Combined: **120 worlds, 600 policy-world runs**; each batch used 20 ring, 20 hub and 20 bridge worlds.
- Model: `gpt-5.4-mini-2026-03-17`, reasoning effort `medium`, default sampling configuration as recorded in the experiment.
- Map reconstruction: directed edge F1 over the **entire ground-truth world map**, matching road ID, origin, destination, travel cost and passability.
- Navigation: at most six actions and 24 simulated travel-time ticks. Efficiency `E` equals the world's feasible minimum travel cost divided by the realized travel cost when the goal is reached; failures score zero.
- Map reconstruction and navigation were evaluated separately from the same starting observations and memory. The generated map **was not passed into** the navigation controller.
- The predesignated main comparison was M minus R for mean navigation efficiency, using within-world paired differences. The reported uncertainty interval comes from 20,000 stratified world-level bootstrap resamples. Follow-up sampling was decided **after** reviewing the first batch; the combined interval was not adjusted for that decision.

## Results

### Primary M versus R comparison

| Batch | Worlds | Mean E: M minus R | Reported 95% interval |
| --- | ---: | ---: | --- |
| First | 60 | -0.0388 | [-0.1058, +0.0241] |
| Follow-up | 60 | +0.0305 | [-0.0555, +0.1155] |
| Combined | 120 | **-0.0041** | **[-0.0583, +0.0488]** |

The mean direction reversed between batches. The combined estimate does not establish that mixed memory performs better than result-agnostic recency memory. An interval spanning zero is **not** proof of equivalence.

### Combined outcomes

| Policy | Full-map edge F1 | Mean navigation E | Goal reached (out of 120) | Goal path connected in initial information (out of 120) |
| --- | ---: | ---: | ---: | ---: |
| N | 0.1720 | 0.6520 | 90 | 0 |
| S | 0.4091 | 0.7387 | 99 | 46 |
| F | 0.4080 | 0.7487 | 102 | 36 |
| M | 0.4049 | 0.7600 | 100 | 36 |
| R | 0.4043 | 0.7641 | 103 | 51 |

The F condition exceeded N in mean E (0.7487 versus 0.6520) and reached more goals (102 versus 90). However, **F had four additional road records and N had none**, so this contrast does not identify the causal effect of a road's failed-mission provenance.

The full-map edge F1 should not be read as a supplied-fact accuracy score: N reproduced the attributes of its supplied roads with mean accuracy 100%, yet its full-map F1 was 0.1720. The other policies' supplied-fact accuracy was approximately 99.65% to 99.93%.

### Full-map score versus navigation

M and R had different E values in 32 worlds, but their full-map edge F1 was the same in 28 of these. In `main-bridge-5`, both map F1 scores were 0.4286, while M reached the goal in three ticks and R in seven (world minimum: three ticks).

Therefore full-map F1 alone does not adequately account for the usefulness of retained paths for a specific goal. This **does not** show that an explicit generated map caused a navigation decision, because the two outputs were scored independently.

## Interpretation limits

- This is evidence about **selection policies and the road content they retain**, not an isolated causal effect of labeling the same roads as successful or failed.
- Results come from one model configuration and three generated world families, with one sampled agent trajectory per world-condition pair; world bootstrap intervals do not separately estimate same-world model response variance.
- The second 60-world batch was added after first-batch results were inspected.
- The supplied histories were controlled inputs rather than agent-acquired long-term autobiographical memory.
- No new live model calls or independent raw SQLite/response re-audit were conducted as part of the 2026-11-24 retrospective.
- Do not conflate these 600 runs with earlier E03 Memory Horizon / Distinct Places mock-preflight work, E04 information-change pilot evidence, or Actual living-simulation experiments.

## Provenance and pending repository synchronization

The outcomes above are transcribed from the final saved research summary as documented in [the MemoryReplica experiment record](https://app.notion.com/p/40de2656dc2681b391f4c8f9fc3ef51c). The follow-up run was reported `FINISHED / ALL_CASES_COMPLETED` at 2026-11-23 04:35 KST after scoring 300/300 cases; the first-batch result had already been completed.

**Pending before marking this work reproducible from GitHub:**

1. Identify the authoritative local worktree and commit/source digest of the tested MemoryReplica implementation, not merely a similarly named older branch.
2. Review code, frozen inputs, result manifests and permitted non-sensitive sample outputs for secrets, personal information and unexpectedly large artifacts.
3. Stage the implementation and tests in a separate branch without overwriting the original run database or historical evidence.
4. Re-run applicable offline tests and, where available, read-only result aggregation/audit against the preserved records. Do **not** run an additional paid LLM experiment.
5. Review the diff and merge only after code and recorded results are clearly linked.

This file is a public **result summary**, not the original experiment dataset.
