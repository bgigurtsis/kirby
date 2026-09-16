# Kirby for Codex

Use **native Luna subagents** for file reading and basic mechanical boilerplate
writes. Your selected main model handles reasoning, integration, and review.
The reader and writer use `gpt-5.6-luna` with medium reasoning effort.

## Install or update

Requires Python **3.11+**, a Codex client with native subagent support and hooks,
and Luna access through your existing ChatGPT/Codex sign-in.

```sh
python3 codex/install.py install
```

Run from this checkout. Windows: use `py -3` instead of `python3`. Then open
Codex `/hooks`, review and trust the entry labelled `Kirby: redirect large
whole-file reads`, and start a new task. The read guard does not run until
trusted. Recorded v1 to v4 installations migrate to v5. Migration preserves
unrelated hooks, settings, and sign-in.

## Use

Three things make Codex delegate, and each one is needed. A rule in global
`AGENTS.md` says that files over 200 lines and questions spanning three or more
files go to `kirby_luna_bulk_reader`. The native reader and writer agents sit in
Codex's own tool list. A `PreToolUse` hook denies any read that would put more
than 200 lines into context and names the native reader as the way through. The
hook follows `cd`, expands globs, adds up several files, and reads `head`, `tail`,
and `sed -n` counts; output piped to a filter or sent to a file is not counted.
Advisory routing on its own, which is what v4 shipped, did not get used.

Small tasks and reads under 200 lines stay with the main model. If native
delegation or Luna is unavailable or blocked, Codex reports the limitation and
continues with targeted reads. It does not automatically retry through the CLI,
another model, or an API key.

The CLI remains installed as an optional tool. Use it only after an explicit CLI
request and authorization for the selected source to be processed by Luna through
the signed-in CLI. See [CLI options](CLI.md).

`KIRBY_CODEX_MIN_LINES` changes the threshold; `KIRBY_CODEX_DISABLE=1` switches the
guard off. The older `SIDETRACK_CODEX_*` names still work.

## Manage

```sh
python3 codex/install.py status
python3 codex/install.py uninstall
```

Removal archives managed files in recoverable backups and removes only Kirby's
recorded hook entry. Workers consume account allowance; model availability and
limits apply. Native delegation remains subject to normal permissions and approval
review.

[Detailed setup](setup.md) - [Historical comparison](CLI-COMPARISON.md) -
[Tests](TESTING.md) - [Official subagent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents)
