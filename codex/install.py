#!/usr/bin/env python3
"""Install Kirby's native Luna subagents, routing rule, read hook, and optional CLI."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tomllib
from datetime import datetime, timezone
from uuid import uuid4

SOURCE = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE))
import hook_config  # noqa: E402
import read_hook  # noqa: E402

THRESHOLD = read_hook.DEFAULT_THRESHOLD
ASSETS = (
    "skills/kirby-luna/scripts/kirby.py",
    "skills/kirby-luna/SKILL.md",
    "agents/kirby_luna_bulk_reader.toml",
    "agents/kirby_luna_code_writer.toml",
    hook_config.HOOK_PATH,
)
CLI_ASSETS = ASSETS[:2]
AGENT_ASSETS = ASSETS[2:4]
NATIVE_ASSETS = ASSETS[:4]
READ_HOOK = ASSETS[4]
SOURCES = {name: name for name in ASSETS}
SOURCES[ASSETS[0]] = "cli.py"
SOURCES[READ_HOOK] = "read_hook.py"
LEGACY_ASSETS = (
    "agents/kirby_luna_bulk_reader.toml",
    "agents/kirby_luna_code_writer.toml",
    "skills/kirby-luna/SKILL.md",
)
STATE = "kirby/install.json"
RULE = "rules/kirby-luna.rules"
START = "<!-- kirby:codex:start -->"
END = "<!-- kirby:codex:end -->"
BLOCK = f"""{START}
## Kirby: Luna delegation

Reading rules for every task:

- Before reading any file over {THRESHOLD} lines, or when one question spans three or more
  files, spawn `kirby_luna_bulk_reader` with the paths and a focused question. It returns
  short findings with line references and the files never enter your context. Use it for
  that job instead of cat, Get-Content, sed, head, tail, or a whole-file read.
- A hook denies any read that would put more than {THRESHOLD} lines into context. The native
  reader is the way through; a different read command is not.
- For follow-up questions spawn the reader again with the same paths.
- Read a file yourself only when you need exact lines to edit, and then only the range the
  reader pointed you to.
- For a new file that copies an existing one with literal substitutions (mirrored tests,
  fixtures, config entries, stubs), spawn `kirby_luna_code_writer` with the reference, the
  target path, and every substitution, then review the diff.

This is a standing request to delegate to native Codex Luna subagents while the main model
continues useful independent work. Where the native tool accepts model selection instead of
custom agents, select `gpt-5.6-luna` with medium reasoning and a focused prompt.
Writers must copy an exact reference with explicit substitutions and no implementation choices.
The main model must handle new logic, refactors, debugging, integrations, and security-sensitive changes.
Workers must return ambiguous or complex tasks without writing files.
Output size and detailed specs must not override these limits.
Keep the selected main model for reasoning, integration, and review. Skip recursive
invocation and automatic routing when the main model is Luna or this is already a delegated
worker. If native Luna delegation is unavailable, report that and continue directly with
targeted reads under {THRESHOLD} lines. Follow the current user's instructions. Do not
automatically launch a separate CLI process as fallback.

Kirby is the renamed Sidetrack workflow. Its current skill is `kirby-luna` and
its optional CLI is `skills/kirby-luna/scripts/kirby.py`. Use that CLI only when
the user requests it and authorizes the selected source to be processed by OpenAI
Luna through their signed-in Codex CLI. Delegation does not authorize other data
transfers, API-key authentication, or bypassing sandbox and approval controls.
If approval review denies an action, report the denial and continue permitted
local work; do not retry the denied transfer through another transport.
{END}"""


class InstallError(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def target(root, relative):
    """Keep all writes within the chosen home; refuse linked managed paths."""
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise InstallError(f"Path escapes Codex home: {path}")
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise InstallError(f"Linked installation path is unsupported: {part}")
    if path.exists() and not path.is_file():
        raise InstallError(f"Expected a file: {path}")
    return path


def read_state(root):
    path = target(root, STATE)
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    expected = {1: set(LEGACY_ASSETS), 2: set(CLI_ASSETS), 3: set(CLI_ASSETS) | {READ_HOOK},
                4: set(NATIVE_ASSETS), 5: set(ASSETS)}.get(state.get("version"))
    actual = set(state.get("hashes", {}))
    if (expected is None or (actual != expected and not (
            state.get("version") in (2, 3, 4, 5) and actual == expected | {RULE}))
            or state.get("instructions") not in ("AGENTS.md", "AGENTS.override.md")
            or not isinstance(state.get("block"), str)
            or not state["block"].startswith(START)
            or not state["block"].endswith(END)):
        raise InstallError("Unrecognized Kirby installation record; no files changed.")
    if state["version"] in (3, 5) and not isinstance(state.get("hook_entry"), dict):
        raise InstallError("Missing managed Kirby hook record; no files changed.")
    return state


def check_owned(root, state):
    for name, expected in state["hashes"].items():
        path = target(root, name)
        if not path.exists() or digest(path.read_bytes()) != expected:
            raise InstallError(f"Installed file changed or missing: {path}. Preserve your changes before reinstalling/removing.")


def route_text(path):
    return path.read_bytes().decode("utf-8") if path.exists() else ""


def check_markers(text, expected=None):
    # Some v3 installs wrote AGENTS.md with Windows newline translation while
    # recording the LF block. Accept only that exact line-ending difference.
    if expected is not None and text.count(expected) == 0:
        crlf = expected.replace("\r\n", "\n").replace("\n", "\r\n")
        if text.count(crlf) == 1:
            expected = crlf
    if expected is None:
        if START in text or END in text:
            raise InstallError("Found Kirby routing without an installation record.")
    elif text.count(START) != 1 or text.count(END) != 1 or text.count(expected) != 1:
        raise InstallError("Kirby routing was modified; preserve your changes before proceeding.")
    return expected


def backup_path(root):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    relative = f"kirby/backups/{stamp}-{uuid4().hex[:8]}"
    target(root, relative + "/check")
    return root / relative


def save(root, changes, retired=()):
    """Back up every replaced file before the first write. Never delete backups."""
    backup = backup_path(root)
    for relative in changes:
        path = target(root, relative)
        if path.exists():
            dest = backup / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
    for relative, content in changes.items():
        if relative == STATE:
            continue
        path = target(root, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for relative in retired:
        dest = backup / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target(root, relative)), str(dest))
    if STATE in changes:
        path = target(root, STATE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(changes[STATE])
    if backup.exists():
        print(f"Backups: {backup}")


def install(root, dry_run=False, allow_luna=False):
    state = read_state(root)
    if state:
        check_owned(root, state)
    # Keep installed assets stable across Git's Windows newline conversion.
    assets = {name: (SOURCE / SOURCES[name]).read_bytes().replace(b"\r\n", b"\n") for name in ASSETS}
    if allow_luna:
        pattern = [str(Path(sys.executable).resolve()), str(target(root, ASSETS[0]))]
        assets[RULE] = (
            "# User opted in with install --allow-luna. Allows this installed script,\n"
            "# including task-relevant private source processing by OpenAI Luna.\n"
            "# Does not authorize arbitrary Python or PowerShell commands.\n"
            "prefix_rule(pattern=" + json.dumps(pattern) + ', decision="allow")\n'
        ).encode("utf-8")
    elif state and RULE in state["hashes"]:
        assets[RULE] = target(root, RULE).read_bytes()
    if RULE in assets:
        print("Luna allow rule covers direct interpreter + installed script calls only.")
        print("PowerShell -Command wrappers do not match; other policies still apply. Restart Codex after installation.")
    compile(assets[ASSETS[0]], ASSETS[0], "exec")
    compile(assets[READ_HOOK], READ_HOOK, "exec")
    for name in AGENT_ASSETS:
        agent = tomllib.loads(assets[name].decode("utf-8"))
        if not all(agent.get(key) for key in ("name", "description", "developer_instructions")):
            raise InstallError(f"Incomplete native agent: {name}")
    hook_path = target(root, "hooks.json")
    hooks_before = (state["hooks_before"] if state and "hooks_before" in state else
                    hook_path.read_bytes().decode("utf-8") if hook_path.exists() else None)
    hook_data, hook_entry = hook_config.prepare(root, state, assets[READ_HOOK])
    override = target(root, "AGENTS.override.md")
    instructions = (state["instructions"] if state else
                    "AGENTS.override.md" if override.exists() and override.stat().st_size else "AGENTS.md")
    if state and instructions == "AGENTS.md" and override.exists() and override.stat().st_size:
        raise InstallError("AGENTS.override.md now shadows the installed routing. Uninstall first, then reinstall.")
    path = target(root, instructions)
    text = route_text(path)
    installed_block = check_markers(text, state["block"] if state else None)
    if state:
        updated = text.replace(installed_block, BLOCK, 1)
        separator = state.get("separator", "")
        if "\r\n" in installed_block and "\r\n" not in state["block"]:
            separator = separator.replace("\n", "\r\n")
    else:
        separator = "" if not text or text.endswith("\n\n") else "\n" if text.endswith("\n") else "\n\n"
        updated = text + separator + BLOCK
    for name, content in assets.items():
        dest = target(root, name)
        if dest.exists() and (not state or name not in state["hashes"]):
            raise InstallError(f"Refusing to overwrite an unowned file: {dest}")
    retired = tuple(name for name in state["hashes"] if name not in assets) if state else ()
    record = {"version": 5, "instructions": instructions, "separator": separator,
              "hook_entry": hook_entry, "hooks_before": hooks_before,
              "block": BLOCK, "hashes": {name: digest(content) for name, content in assets.items()}}
    proposed = {**assets, "hooks.json": hook_data, instructions: updated.encode("utf-8"),
                STATE: (json.dumps(record, indent=2) + "\n").encode("utf-8")}
    changes = {name: data for name, data in proposed.items()
               if not target(root, name).exists() or target(root, name).read_bytes() != data}
    if not changes:
        print("Kirby is already installed and up to date.")
        return
    for name in changes:
        print(f"{'Would write' if dry_run else 'Install'}: {root / name}")
    for name in retired:
        print(f"{'Would archive' if dry_run else 'Archive retired asset'}: {root / name}")
    if not dry_run:
        save(root, changes, retired)
        print("Installed. Open Codex /hooks, review and trust the Kirby entry, then start a new task.")
        print("The read guard is inactive until trusted. Your main model, config, and sign-in are unchanged.")


def uninstall(root, dry_run=False):
    state = read_state(root)
    if not state:
        print("Kirby is not installed.")
        return
    check_owned(root, state)
    hook_data = None
    if state.get("hook_entry"):
        target(root, "hooks.json")
        hook_data, _ = hook_config.prepare(root, state)
    relative = state["instructions"]
    path = target(root, relative)
    text = route_text(path)
    installed_block = check_markers(text, state["block"])
    # Remove only our block and the separator we appended; preserve later user text.
    separator = state.get("separator", "")
    if "\r\n" in installed_block and "\r\n" not in state["block"]:
        separator = separator.replace("\n", "\r\n")
    chunk = separator + installed_block
    updated = text.replace(chunk if chunk in text else installed_block, "", 1)
    for name in (*state["hashes"], STATE):
        print(f"{'Would archive' if dry_run else 'Archive'}: {root / name}")
    if dry_run:
        return
    backup = backup_path(root)
    dest = backup / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    if hook_data is not None:
        hook_path = root / "hooks.json"
        shutil.copy2(hook_path, backup / "hooks.json")
        if state.get("hooks_before") is None and json.loads(hook_data) == {}:
            hook_path.unlink()  # the file only existed because Kirby created it
        else:
            hook_path.write_bytes(hook_data)
    path.write_bytes(updated.encode("utf-8"))
    for name in (*state["hashes"], STATE):
        archived = backup / name
        archived.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target(root, name)), str(archived))
    print(f"Removed active Kirby files. Recoverable copies: {backup}")


def status(root, dry_run=False):
    state = read_state(root)
    if not state:
        print("Kirby is not installed.")
        return
    check_owned(root, state)
    check_markers(route_text(target(root, state["instructions"])), state["block"])
    override = target(root, "AGENTS.override.md")
    if state["instructions"] == "AGENTS.md" and override.exists() and override.stat().st_size:
        raise InstallError("Routing is shadowed by AGENTS.override.md; uninstall and reinstall.")
    if state.get("hook_entry"):
        target(root, "hooks.json")
        hook_config.prepare(root, state)  # raises if the recorded entry was changed or removed
    method = ("native Luna subagents, read hook, and optional CLI" if state["version"] == 5
              else "legacy installation; run install to migrate")
    print(f"Installed: {method} and kirby-luna skill under {root}")
    routing = "blocking read hook; trust it in /hooks" if state.get("hook_entry") else "advisory; no blocking hooks"
    print(f"Routing: {state['instructions']} ({routing})")
    print(f"Direct Luna command allow rule: {'enabled' if RULE in state['hashes'] else 'disabled'}")
    print("Account/model availability is not checked. Start a new task and run the native smoke test.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "status", "uninstall"), nargs="?", default="install")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"))
    parser.add_argument("--dry-run", action="store_true", help="Show planned changes without writing")
    parser.add_argument("--allow-luna", action="store_true", help=(
        "Opt in to persistent approval of the installed Kirby script through this Python "
        "interpreter, including sending task-relevant private source to OpenAI Luna via "
        "your signed-in Codex CLI. Does not match PowerShell -Command wrappers."))
    args = parser.parse_args(argv)
    if args.allow_luna and args.action != "install":
        parser.error("--allow-luna requires install")
    try:
        root = args.codex_home.expanduser().resolve()
        if args.action == "install":
            install(root, args.dry_run, args.allow_luna)
        else:
            globals()[args.action](root, args.dry_run)
    except (InstallError, OSError, ValueError, SyntaxError) as exc:
        print(f"Kirby: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
