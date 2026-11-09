---
name: kirby-read
description: Delegate reading of large files (or many files) to a cheap worker model (Claude Haiku by default) and get a short bulleted answer instead of loading the files into context. Use for any file over the kirby threshold (200 lines by default), for any question that spans several files, and whenever the kirby hook blocks a read.
---

# kirby-read

The session rule and the `kirby_read` tool description say when to delegate a read. This page covers what they have no room for: how to ask, what to check, and when to read the file yourself.

## Invocation

Call the `kirby_read` tool with `question` and `paths`. Globs work, for example `src/**/*.py`. If your client lists the tool as deferred, load it first. If the tool is absent, run the same worker from the shell:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/kirby.py" read --question "<question>" --paths <file> [<file> ...]
```

## How to ask

The worker answers at the length the question allows. In testing on a 600-line file, a one-bullet question took 4 seconds. A request to list every function and constant took 76 seconds and billed 9,155 output tokens.

- Ask one thing per call. After the first call the worker reads the files from cache, so three narrow calls cost less than one six-part question.
- Say how long the answer should be: "one bullet", "at most ten bullets", "names and line numbers only".
- Ask for the quoted line when you plan to read or edit that spot. kirby turns each quote into a file and line.

Examples:

- "Where is retry and backoff implemented? One bullet, with the quoted def line."
- "Which functions perform network I/O? Quote each def line and nothing else."
- "Summarise the data model in at most ten bullets: classes, fields, relationships."

## What to check

- Each answer ends with a Locations list. kirby searches the files for every whole line the worker quoted and prints the file and line when that text occurs exactly once. Code produces those numbers, so pass them straight to `Read` as an offset. Ignore any line number the worker states itself. In testing the worker's own numbers were wrong more often than right.
- A quote with no entry in the list is not a line of the file, or it occurs more than once. Grep for it.
- A summary, or a claim that nothing does X, is the worker's judgement. Read the range before you act on it.
- The usage line at the end of each answer names the model that ran. An older model than you expect means the `claude` on PATH is out of date. The Claude Code guide in `docs/claude-code.md` has the fix.

## When to read the file yourself

- Debugging subtle bugs, concurrency, security-sensitive logic, and code review. Ask kirby where the code is, then read those ranges, each under the threshold.
- Files under the threshold.
