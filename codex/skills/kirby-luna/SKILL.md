---
name: kirby-luna
description: Delegate substantial file reading and only basic mechanical boilerplate writes to native Codex Luna subagents while the main model handles reasoning, integration, and review. Small targeted tasks stay with the main model.
---

# Kirby Luna delegation

Use native Codex subagents for bounded work that can run independently alongside
useful work by the main model. This skill explicitly requests that delegation.
Keep the selected main model unchanged. Any file over 200 lines, or a question that
spans three or more files, goes to the reader. A hook denies reads over that size and
names the reader as the way through; do not fetch the same file through another command.
Output size must not qualify a writing task.

## Native reader and writer

- Use `kirby_luna_bulk_reader` for focused source questions. Give it the workspace,
  relevant paths, a question, and the desired concise findings with line references.
- Use `kirby_luna_code_writer` only for basic mechanical boilerplate under the writing limits below.
  The parent must provide an exact reference, target, and complete substitutions.
  The parent must review the diff and run relevant checks.
- If the native tool supports model selection rather than named custom agents,
  select `gpt-5.6-luna`, medium reasoning. Use a fresh context with a self-contained
  prompt and paths; do not inherit the whole conversation for routine work. Tell
  the worker not to delegate or invoke the Kirby CLI.
- Continue useful independent work, then collect the result before integrating it.
  Verify important findings against targeted source sections. Keep architecture,
  difficult debugging, security decisions, and final review with the main model.
- Skip recursive delegation when already a worker or when the main model is Luna.
  If there is no suitable independent task, use direct targeted reads or edits.
- If native tools, a slot, or Luna are unavailable, report the limitation and work
  directly. Do not silently switch models or launch the CLI as an automatic fallback.

## Writing limits

These limits must apply to native writers and the optional CLI.

1. The parent must confirm that the write only copies a reference with explicit substitutions or supplied literal values.
2. The parent must keep tasks that need new logic, inferred behavior, or implementation choices.
3. The worker must return ambiguous or complex tasks to the parent without writing files.

Workers may duplicate static fixtures, declarations, or config entries with exact supplied values.
Workers may copy a trivial function or test only when behavior and assertions remain unchanged.
The parent must choose test cases and expected results.
The parent must handle algorithms, validation, error handling, refactors, debugging, integrations, and behavior that spans files.
The parent must handle security-sensitive code and config that changes access, deployment, or data handling.
A detailed spec, large output, or low temperature must not override these limits.
The parent must not split complex implementation into small worker assignments to bypass these limits.
The parent must prefer direct edits when uncertain.

Native agents use the current Codex session's permission controls. This is hosted
model processing, not offline inference. Restrict context and file access to the
user's task; delegation alone does not authorize deployment or messaging.

## Optional CLI

Use `scripts/kirby.py` relative to this skill only when the user requests the CLI
and authorizes the selected source to be processed by OpenAI Luna through their
signed-in Codex CLI. Installation and native delegation are not CLI transfer consent.
Use Python 3.11+, the absolute script path, and an explicit workspace:

```sh
python3 /path/to/skill/scripts/kirby.py --workspace /path/to/project read --question "Which services retry failed requests?" --paths src/services.py src/config.py
python3 /path/to/skill/scripts/kirby.py --workspace /path/to/project write --spec "Copy the reference test, replacing only the supplied literal label" --reference tests/test_users.py --context src/orders.py --target tests/test_orders_new.py
```

Inputs and outputs must stay inside the workspace. Split inputs over 500 KB;
outputs must be new files in existing directories. Stage changes to existing files,
then review and apply them. The CLI requires ChatGPT sign-in and Luna availability;
never switch to API-key authentication or copy credentials into the workspace.

Use normal sandbox and narrowly scoped approval controls. If automatic approval
review rejects a CLI transfer, report the denied action and reason, then continue
permitted local work. Do not retry that transfer with another wrapper, service,
or subagent as a way around the denial. A command allow rule does not override
managed policy or guarantee automatic approval.

Stdout is the result; stderr contains elapsed time and Codex-reported usage.
Optional `--timeout 180` and `--report metrics.json` go before the mode; reports
require new paths. Token counts are not subscription credits.
