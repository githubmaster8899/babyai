# Multi-agent orchestration

BabyAI can now plan a larger request, represent it as a small task graph, hand
tasks to specialised workers, verify their results, and combine them into one
reply. This borrows only general ideas from systems such as ARTEX — planning,
task graphs, workers, shared memory, orchestration and result verification. No
ARTEX code and no offensive-security behavior is included.

## What stays the same

- Ordinary chat goes straight to the existing `BabyAI` agent. Nothing changes
  unless you ask for a plan (or explicitly turn on the auto heuristic).
- The default tool set is unchanged. No `delegate_task`, `create_plan` or other
  model-visible orchestration tools are exposed.
- Every worker tool call uses the existing `ToolRegistry`, `SafetyGate` and the
  front end's confirmation hook. Confirmation is fail-closed: no hook means no.
- The planner, coordinator, workers, verifier and task store are protected from
  BabyAI's self-upgrade tool, alongside the safety and guardrail files.

## Using it

Start a message with `plan:` or `orchestrate:` in the CLI or desktop overlay:

```text
plan: research the local notes, then write a short summary and check it against the source
```

`/plan ...` also works. The coordinator:

1. asks the planning model for a JSON task graph;
2. validates ids, dependencies and cycles in Python;
3. stores the plan in SQLite;
4. runs tasks in dependency order (independent tasks can run together if
   `max_workers > 1`);
5. verifies each result before dependants may use it;
6. returns a short, deterministic summary.

If the planner cannot produce a valid graph, BabyAI falls back to one general
worker rather than failing the request.

## Workers

| Role | Purpose |
| --- | --- |
| `research` | Gather facts from local files, memory and allowed web tools. |
| `coding` | Read, search, edit and test with the existing tools, under the same approvals. |
| `memory` | Recall or store local facts with provenance. |
| `verification` | Check artifacts against the requested outcome. |
| `general` | Fallback for work that has no specialism. |

Workers get separate conversations but share the assistant's tools, memory,
safety gate and confirmation lock. Tool output is always framed as untrusted
data, never as instructions.

## Configuration

Add a `[orchestrator]` section to `config.toml`. All keys are optional.

```toml
[orchestrator]
enabled = true            # false delegates every message to ordinary BabyAI
auto = false              # true enables the conservative complex-request heuristic
max_tasks = 8
max_workers = 1           # >1 runs independent tasks in parallel
max_tool_calls = 4        # per worker task
max_rounds = 3            # model <-> tools rounds per worker task
max_retries = 1
verify_with_llm = false   # deterministic checks always run; this adds a model opinion
planner_model = ""        # empty uses [llm] model
worker_model = ""
verifier_model = ""

# [orchestrator.models] can override a single role, e.g. coding = "qwen2.5-coder"
dir = "~/.local/share/babyai/orchestrator"
```

In portable mode `dir` is redirected to `<stick>/data/orchestrator` and created
with the other data folders. The database uses the same `DELETE`/`FULL` SQLite
settings as memory, so no `-wal` file is left on the stick.

## Verification and shared memory

A deterministic verifier rejects empty results, tool failures, safety-gate
refusals and user declines. With `verify_with_llm = true` it also asks a model
whether each artifact meets the task's `expected_artifact`; a failed check is
retried up to `max_retries`. Failed tasks block their dependants rather than
letting bad results spread through the graph.

Successful worker summaries are written both to the plan's SQLite store and to
BabyAI's ordinary memory with a `source` such as
`agent:coding:t2`, so later tasks and later conversations can see where a fact
came from.

## Progress display

The coordinator calls an optional text hook with short, fixed lines such as
`[orchestrator] 2/4 coding: update the notes`. The desktop overlay receives
these through a queue and shows the latest one in its thinking line; worker
threads never touch Tk directly. The CLI leaves progress off so one-shot output
stays clean.

## Tests

```sh
python3 -m unittest tests.test_orchestrator -v
python3 -m unittest discover -s tests -t .
```

The tests cover graph validation and ordering, dependency artifacts, safety-gate
use, fail-closed and approved confirmation, verifier retries, task-store
durability, portable paths, normal-chat fallback and the unchanged tool set.

## Limits

- One plan per `plan:` message; plans are not interactive or resumable from the UI.
- The coordinator's final summary is deterministic rather than model-written.
- Model-based verification is opt-in; deterministic verification is the default.
- Parallel workers share the confirmation lock, so approval prompts appear one
  at a time. This is deliberate: the user must never lose track of what was
  approved.
