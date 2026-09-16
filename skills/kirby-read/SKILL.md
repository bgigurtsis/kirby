---
name: kirby-read
description: Delegate reading of large files (or many files) to a cheap worker model (Claude Haiku by default) and get a short bulleted answer instead of loading the files into context. Use for any file over the kirby threshold (200 lines by default), for any question that spans several files, and whenever the kirby hook blocks a read.
---

# kirby-read

Send files plus a question to the worker model (Haiku by default, or Luna via the openai backend). Only the answer enters your context. The files never do.

## Invocation

Call the `kirby_read` tool with `question` and `paths`. When the tool is not listed, the same worker runs from the shell:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/kirby.py" read --question "<question>" --paths <file> [<file> ...]
```

Globs are accepted (for example `src/**/*.py`). Token usage and cost are printed to stderr, and the tool appends them to its answer.

## When to use

- Any file over the threshold (200 lines by default). The hook blocks reading it whole anyway.
- The question is about *what* code does: structure, call graph, which functions touch X, how a module is organised.
- The same question spans several files. Send them all in one call.
- Follow-up questions: call again with the same paths. Re-sending is cheap because the files never enter your context.

## When NOT to use

- You need exact line numbers to edit. Ask *where* something lives (the worker quotes the surrounding line), then `Grep` for that line and `Read` only that range, under the threshold.
- Debugging subtle bugs, concurrency, security-sensitive logic, or architectural decisions. Read those yourself, in ranges under the threshold.
- Files under the threshold. Just read them.

## Good questions

Be specific and ask for structure:

- "List every public function with a one-line purpose and its parameters."
- "Which functions perform network I/O, and what do they call?"
- "Where is retry/backoff implemented? Quote the def line."
- "Summarise the data model: classes, fields, relationships."

## Escape hatch

`KIRBY_DISABLE=1` in the environment makes the hooks allow everything and drops the session rule. `KIRBY_MIN_LINES` changes the threshold (default 200).
