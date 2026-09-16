---
name: kirby-write
description: Delegate basic mechanical boilerplate to a cheap worker model (Claude Haiku by default). The worker copies an exact reference file with explicit substitutions and writes the result straight to disk. Use for mirrored tests, fixtures, config entries, and stubs. Not for anything that needs a design decision.
---

# kirby-write

Generate a file from a spec plus a reference file, using the worker model (Haiku by default, or Luna via the openai backend). The output goes straight to disk. Only a short confirmation enters your context.

## Invocation

Call the `kirby_write` tool with `spec`, `reference`, `target`, and optionally `context`. When the tool is not listed, the same worker runs from the shell:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/kirby.py" write --spec "<spec>" --reference <file> [--context <file> ...] --target <path> [--force]
```

The target must not exist unless you pass `--force` (or `force: true`). Token usage and cost are printed to stderr, and the tool appends them to its answer.

## When to use

- A new file that copies an existing one with literal substitutions: a mirrored test module, a fixture, a config entry, a stub.
- You can name every substitution. "Replace orders with users, Order with User, and the label text" is a spec. "Make it work for users" is not.

## When NOT to use

- New logic, refactors, debugging, integrations, or security-sensitive code. Write those yourself.
- Anything where the worker would have to choose an approach. If the spec has a gap, the worker returns without writing and you fill the gap.
- Output size alone. A long file that needs judgement is still your job.

## Rules

- Always give a reference file. The worker copies its shape and conventions.
- List every substitution explicitly in the spec.
- Review the diff before accepting it. Run the relevant checks.
- The worker exits with a clear message instead of guessing when the task is ambiguous or complex. Take it back when that happens.

## Escape hatch

`KIRBY_DISABLE=1` in the environment switches the hooks and the session rule off. The tool still works when called.
