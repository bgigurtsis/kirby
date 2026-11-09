# Claude Code setup

kirby is a Claude Code plugin that sends big file reads and boilerplate generation to Claude Haiku, so your main model only sees the answer.

Works with a Claude Code subscription alone. No API key, no extra service. Haiku is called through `claude -p`, so it bills against the plan you already have. If you have an OpenAI key you can switch the worker to GPT-5.6 Luna instead (see [Using Luna](#using-luna-with-an-openai-api-key)).

## Setup

Requires Claude Code and Python 3.10+ on your PATH.

```bash
claude plugin marketplace add bgigurtsis/kirby
claude plugin install kirby@kirby
```

Start a new Claude Code session. The first time Claude calls `kirby_read`, pick "always allow" at the prompt, or add the rule to `~/.claude/settings.json` up front:

```json
{ "permissions": { "allow": ["mcp__plugin_kirby_kirby__kirby_read"] } }
```

Without that rule a permission prompt sits between the model and the delegate, and headless runs (`claude -p`) refuse the tool outright, so the model falls back to slices under the threshold. `kirby_write` writes files, so leave it behind the prompt.

## What it does

Three things make the model reach for kirby. Each closes a gap the previous version left open.

**A standing rule.** A SessionStart hook puts a short rule into context at startup, on resume, and after compaction: files over 200 lines and questions that span three or more files go to `kirby_read`. In ten days of transcripts the skill descriptions alone never triggered a single delegation. A rule in context does.

**Two tools.** A bundled MCP server (stdio, standard library only) registers `kirby_read` and `kirby_write` in the tool list. Models pick tools from the list far more readily than they load skills. The same worker still runs from the shell:

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/kirby.py" read --question "Which functions touch the database?" --paths src/service.py src/handler.py
python "$CLAUDE_PLUGIN_ROOT/scripts/kirby.py" write --reference tests/test_orders.py --target tests/test_users.py --sub Order User --sub orders users
```

**Hooks that block the alternatives.** Before every Read, Bash, and PowerShell call a hook refuses anything that would put more than 200 lines into context, and the refusal names `kirby_read` as the way through. That covers whole-file Reads and Read windows over the limit; `cat`, `type`, `less`, `more`, and `Get-Content`; `head` and `tail` with a large count; `sed -n` ranges and `sed` without `-n`. The Bash hook follows `cd` inside the command, expands globs, adds up several files, treats `2>&1` as still printing, and understands Git Bash `/c/` paths on Windows. Output that goes to a pipe or a file is not counted.

`kirby_read` sends files plus a question to the worker and returns a short bulleted answer. The worker quotes the lines that answer the question. kirby then searches the files for each quoted line and appends its file and line number, so Claude can go straight to a ranged Read. The worker's own line numbers were unreliable in testing, which is why code does the lookup. The files go in the worker's system prompt, which the worker caches, so a follow-up question about the same files reads them from cache. The files never enter your main context.

`kirby_write` makes a new file from a reference file and writes it straight to disk. Given a list of literal substitutions, it copies in code with no model call. Given a spec instead, the worker retypes the file. Both return a diff against the reference, which Claude must review before accepting the file.

## Settings

Set these in your shell or in the `env` block of `~/.claude/settings.json`.

| Variable | Default | What it does |
|---|---|---|
| `KIRBY_MIN_LINES` | `200` | Reads that would put more lines than this into context get redirected |
| `KIRBY_BACKEND` | `claude` | `claude` uses Haiku through your subscription. `openai` uses an API key, see below |
| `KIRBY_MODEL` | `haiku` | Worker model for the `claude` backend, any value `claude --model` accepts |
| `KIRBY_DISABLE` | unset | Set to `1` to switch the hooks and the session rule off |
| `KIRBY_CLAUDE_BIN` | auto | Path to `claude` if it isn't on your PATH |

## Which model answers

The usage line at the end of every answer names the model that ran, for example `model=claude-haiku-5-5`. Check it once after installing.

`KIRBY_MODEL` defaults to the alias `haiku`, and each `claude` binary decides what that alias means. The `kirby_read` and `kirby_write` tools run inside an MCP server, which does not receive `CLAUDE_CODE_EXECPATH`, so they use the `claude` on your PATH. If that binary is older than the one running your session, `haiku` can mean an older model. In one test a PATH binary at 2.1.288 chose `claude-haiku-4-5-20251001` while the session's own 2.1.317 chose `claude-haiku-5-5`, and the older model cost about eight times as much for the same question.

Two fixes, either one:

- Update the `claude` on your PATH.
- Set `KIRBY_MODEL` to a full model id, or `KIRBY_CLAUDE_BIN` to the binary you want, in the `env` block of `~/.claude/settings.json`.

## Using Luna with an OpenAI API key

Not the default. Pick this if you have an OpenAI key and want GPT-5.6 Luna as the worker instead of Haiku. Luna is cheaper per token and answers in a couple of seconds, because the call goes straight to the API instead of through the Claude Code CLI.

1. Put your key on one line in `~/.claude/kirby/openai_key`, or export `OPENAI_API_KEY`.
2. Add to `~/.claude/settings.json`:

```json
{ "env": { "KIRBY_BACKEND": "openai" } }
```

Optional overrides: `KIRBY_OPENAI_MODEL` (default `gpt-5.6-luna`), `KIRBY_OPENAI_EFFORT` (default `low`), `KIRBY_OPENAI_BASE_URL` for any OpenAI-compatible endpoint, `KIRBY_OPENAI_KEY_FILE` to read the key from elsewhere.

## What it doesn't do

- **Edits.** The worker answers questions about a file and never changes one. Ask it where something lives, then do a targeted read of the lines it cites before editing.
- **Reasoning.** Debugging, architecture, and security-sensitive code stay with your main model. The skills say so.
- **Small files.** Below the threshold, delegation costs more time than it saves.
- **Sessions that skip user settings.** The Claude Agent SDK and `claude -p --setting-sources ""` do not load plugins, so nothing here applies to them.

Each worker call is a round trip through the Claude Code CLI. A one-bullet answer about a 600-line file took about 4 seconds in testing. Time grows with the length of the answer, so narrow questions are faster. A worker copy retypes the whole file and can take a minute or two. A literal copy with substitutions makes no model call.

## Development

```bash
python -m pytest tests
```

If pytest cannot create its default temp directory, add `--basetemp` with a writable path.

## Manual install

If you'd rather not use the plugin system, clone the repo and add this to `~/.claude/settings.json`, replacing the path:

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command", "command": "python \"/path/to/kirby/scripts/kirby.py\" hook-session", "timeout": 10 }] }
    ],
    "PreToolUse": [
      { "matcher": "Read", "hooks": [{ "type": "command", "command": "python \"/path/to/kirby/scripts/kirby.py\" hook-read", "timeout": 10 }] },
      { "matcher": "Bash|PowerShell", "hooks": [{ "type": "command", "command": "python \"/path/to/kirby/scripts/kirby.py\" hook-bash", "timeout": 10 }] }
    ]
  }
}
```

Register the tools:

```bash
claude mcp add --scope user kirby -- python /path/to/kirby/scripts/kirby.py mcp
```

Then copy the two folders under `skills/` into `~/.claude/skills/`.

## Credit

The idea and hook design come from Spotify's [shunt](https://github.com/spotify/portal-ai-plugins) plugin, which routes through Portal by Spotify. kirby does the same thing with nothing but Claude Code.

MIT licensed.
