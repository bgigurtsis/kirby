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
python "$CLAUDE_PLUGIN_ROOT/scripts/kirby.py" write --spec "Copy the reference test with only the literal label orders replaced by users" --reference tests/test_orders.py --context src/users.py --target tests/test_users.py
```

**Hooks that block the alternatives.** Before every Read, Bash, and PowerShell call a hook refuses anything that would put more than 200 lines into context, and the refusal names `kirby_read` as the way through. That covers whole-file Reads and Read windows over the limit; `cat`, `type`, `less`, `more`, and `Get-Content`; `head` and `tail` with a large count; `sed -n` ranges and `sed` without `-n`. The Bash hook follows `cd` inside the command, expands globs, adds up several files, treats `2>&1` as still printing, and understands Git Bash `/c/` paths on Windows. Output that goes to a pipe or a file is not counted.

`kirby_read` sends files plus a question to the worker and returns a short bulleted answer. Ask again with the same files for follow-ups. The files never enter your main context. `kirby_write` generates boilerplate from a spec and a reference file, then writes it straight to disk. Claude must review the generated diff before accepting it.

## Settings

Set these in your shell or in the `env` block of `~/.claude/settings.json`.

| Variable | Default | What it does |
|---|---|---|
| `KIRBY_MIN_LINES` | `200` | Reads that would put more lines than this into context get redirected |
| `KIRBY_BACKEND` | `claude` | `claude` uses Haiku through your subscription. `openai` uses an API key, see below |
| `KIRBY_MODEL` | `haiku` | Worker model for the `claude` backend, any value `claude --model` accepts |
| `KIRBY_DISABLE` | unset | Set to `1` to switch the hooks and the session rule off |
| `KIRBY_CLAUDE_BIN` | auto | Path to `claude` if it isn't on your PATH |

## Using Luna with an OpenAI API key

Not the default. Pick this if you have an OpenAI key and want GPT-5.6 Luna as the worker instead of Haiku. Luna is cheaper per token and answers in a couple of seconds, because the call goes straight to the API instead of through the Claude Code CLI.

1. Put your key on one line in `~/.claude/kirby/openai_key`, or export `OPENAI_API_KEY`.
2. Add to `~/.claude/settings.json`:

```json
{ "env": { "KIRBY_BACKEND": "openai" } }
```

Optional overrides: `KIRBY_OPENAI_MODEL` (default `gpt-5.6-luna`), `KIRBY_OPENAI_EFFORT` (default `low`), `KIRBY_OPENAI_BASE_URL` for any OpenAI-compatible endpoint, `KIRBY_OPENAI_KEY_FILE` to read the key from elsewhere.

## What it doesn't do

- **Edits.** The worker's summaries don't carry reliable line numbers. Ask it where something lives, then grep and do a targeted read before editing.
- **Reasoning.** Debugging, architecture, and security-sensitive code stay with your main model. The skills say so.
- **Small files.** Below the threshold, delegation costs more time than it saves.
- **Sessions that skip user settings.** The Claude Agent SDK and `claude -p --setting-sources ""` do not load plugins, so nothing here applies to them.

Each call is a round trip through the Claude Code CLI. Reads take around 7 seconds. Generating a whole file can take a minute or two.

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
