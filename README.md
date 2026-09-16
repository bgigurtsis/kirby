# Kirby

Send large file reads and basic mechanical boilerplate writes to a smaller model. Keep your
chosen main model for reasoning and review. Uses your existing subscription
sign-in, with no Portal or API key.

| Client | Worker | Routing |
| --- | --- | --- |
| Claude Code | Haiku, or Luna with an OpenAI key (opt-in) | A session rule, `kirby_read` and `kirby_write` tools, and hooks that refuse reads over 200 lines |
| Codex | Native Luna subagents | An `AGENTS.md` rule, native reader and writer agents, and a hook that denies reads over 200 lines |

## Install

**Claude Code** - requires Python 3.10+:

```sh
claude plugin marketplace add bgigurtsis/kirby
claude plugin install kirby@kirby
```

**Codex** - requires Python 3.11+, a current Codex client, and Luna access:

```sh
git clone https://github.com/bgigurtsis/kirby.git
cd kirby
python3 codex/install.py install
```

On Windows, use `py -3` instead of `python3`. Then open Codex `/hooks`, trust the
Kirby entry, and start a new task.

Codex defaults to native Luna reader and writer agents. The CLI remains optional
for explicitly requested, authorized calls. `install --allow-luna` adds an opt-in
direct-command permission; it does not resolve automatic approval rejections.
Read the [consent scope and Windows wrapper limitation](codex/setup.md) first.

## How it works

1. Your main model gives the worker a focused question or specification and file paths.
2. **Bulk-reader** reads the files and returns concise findings. **Code-writer** may copy only basic boilerplate with explicit substitutions. The main model must review the diff.
3. Your main model reviews the findings or changes and checks the result.

```text
+--------------+   (1) question or spec + file paths   +----------------+
|              | ------------------------------------> |                |
|  Main model  |                                       |     Worker     |
|   (yours)    | <------------------------------------ |  Haiku / Luna  |
|              |   (2) short findings, or a file       |                |
+--------------+       written straight to disk        +----------------+
```

Small tasks stay with the main model. Debugging, architecture, and final review
stay there too.

Both clients get the same three pieces. A standing rule in context says what to
delegate. The worker is in the tool list, as `kirby_read` and `kirby_write` in
Claude Code and as the native reader and writer agents in Codex. A hook runs before
every file read and shell command and refuses anything that would put more than
200 lines into context, naming the delegate as the way through. The hooks follow
`cd`, expand globs, add up several files, and read `head`, `tail`, and `sed -n`
counts. An earlier version relied on skill descriptions in Claude Code and on an
advisory note in Codex, and ten days of transcripts showed the model never
delegating on its own. If native delegation or Luna is unavailable or blocked,
Codex reports that and continues with targeted direct work, without an automatic
CLI retry. Installation migrates recorded older Codex workflows and removes only
Kirby's recorded read hook. Workers consume subscription allowance; savings vary
and are not guaranteed.

## Using Luna as the worker in Claude Code

By default the Claude Code plugin uses Haiku, called through your Claude subscription. You can switch the worker to GPT-5.6 Luna instead if you have an OpenAI API key.

Why you might want to:

- **Cheaper.** Luna's per-token price is roughly half of Haiku's, and the worker handles the bulk of the tokens.
- **Faster.** Calls go straight to the OpenAI API and return in a couple of seconds. The Haiku path goes through the Claude Code CLI and takes 7 to 10 seconds.
- **Keeps your Claude plan for your main model.** Worker usage bills to your OpenAI account, so heavy delegation does not eat into your Claude rate limits.

Why you might not: you need an OpenAI key, and Luna does not know Claude Code's conventions, so the reference-file requirement matters more for generated code.

Setup, after installing the plugin:

1. Save your key on one line in `~/.claude/kirby/openai_key`, or export `OPENAI_API_KEY`.
2. Add this to `~/.claude/settings.json` (merge into an existing `env` block if you have one):

```json
{ "env": { "KIRBY_BACKEND": "openai" } }
```

3. Start a new Claude Code session.

To use a different model or an OpenAI-compatible endpoint, set `KIRBY_OPENAI_MODEL` or `KIRBY_OPENAI_BASE_URL` in the same `env` block. Full details in [the Claude Code guide](docs/claude-code.md#using-luna-with-an-openai-api-key).

## Guides

- [Claude Code setup, settings, and manual install](docs/claude-code.md)
- [Codex setup and removal](codex/README.md)
- [Codex test results](codex/TESTING.md) · [Run a live test](codex/smoke-test.md)

Inspired by Spotify's [shunt](https://github.com/spotify/portal-ai-plugins).
[MIT licensed](LICENSE).
