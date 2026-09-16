"""Merge only Kirby's recorded hook entry, preserving unrelated hooks."""

import copy
import hashlib
import json
import os
import shlex
import subprocess
import sys

STATUS = "Kirby: redirect large whole-file reads"
LEGACY_STATUS = "Sidetrack: redirect large whole-file reads"
HOOK_PATH = "skills/kirby-luna/scripts/read_hook.py"
MATCHER = "^Bash$|^exec_command$|^shell_command$|^Read$|^read_file$|__read_file$"


def prepare(root, old_state, hook_bytes=None):
    path = root / "hooks.json"
    original = path.read_bytes() if path.exists() else None
    document = json.loads(original.decode("utf-8")) if original else {}
    if not isinstance(document, dict) or not isinstance(document.get("hooks", {}), dict):
        raise ValueError("Unsupported hooks.json structure; no changes made.")
    document = copy.deepcopy(document)
    hooks = document.setdefault("hooks", {})
    entries = hooks.setdefault("PreToolUse", [])
    if not isinstance(entries, list):
        raise ValueError("hooks.PreToolUse must be an array.")
    old_entry = (old_state or {}).get("hook_entry")
    if old_entry is not None:
        if entries.count(old_entry) != 1:
            raise ValueError("Installed Kirby hook was modified or removed; preserve your edits first.")
        entries.remove(old_entry)
    if any(isinstance(e, dict) and any(isinstance(h, dict) and h.get("statusMessage") in (STATUS, LEGACY_STATUS)
           for h in e.get("hooks", [])) for e in entries):
        raise ValueError("An unrecorded Kirby hook already exists; refusing to duplicate it.")
    entry = None
    if hook_bytes is not None:
        argv = [sys.executable, str(root / HOOK_PATH), hashlib.sha256(hook_bytes).hexdigest()]
        command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        entry = {"matcher": MATCHER,
                 "hooks": [{"type": "command", "command": command, "timeout": 5, "statusMessage": STATUS}]}
        entries.append(entry)
    elif not entries:
        hooks.pop("PreToolUse")
        if not hooks:
            document.pop("hooks")
    # Restore exact original bytes if the user has not changed other hooks.
    if hook_bytes is None and old_state and "hooks_before" in old_state:
        before = old_state["hooks_before"]
        original_document = json.loads(before) if before else {}
        if document == original_document:
            return (before or "{}\n").encode("utf-8"), None
    return (json.dumps(document, indent=2) + "\n").encode("utf-8"), entry
