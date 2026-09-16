"""Test actual hook decisions and shared configuration ownership."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).parents[1]
sys.path.insert(0, str(SOURCE))
import read_hook
import hook_config


class ReadHookTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kirby-hook-")).resolve()
        (self.root / "large file.py").write_text("value = 1\n" * 250)
        (self.root / "small.py").write_text("value = 1\n" * 200)
        (self.root / "a.py").write_text("value = 1\n" * 120)
        (self.root / "b.py").write_text("value = 1\n" * 120)
        (self.root / "sub").mkdir()
        (self.root / "sub" / "deep.py").write_text("value = 1\n" * 250)

    def event(self, command, tool="Bash"):
        return {"hook_event_name": "PreToolUse", "model": "gpt-5.6-terra",
                "cwd": str(self.root), "tool_name": tool, "tool_input": {"command": command}}

    def denied(self, command):
        result = read_hook.decision(self.event(command))
        return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_whole_reads_blocked(self):
        for command in ["cat 'large file.py'", 'Get-Content -Raw -LiteralPath "large file.py"',
                        "cat -n 'large file.py'", "head -n 999 'large file.py'",
                        "head -n -1 'large file.py'", "Get-Content 'large file.py'\ncat small.py | head",
                        'powershell -Command "Get-Content -Raw \'large file.py\'"',
                        'python -c "print(Path(\'large file.py\').read_text())"',
                        "cat 'large file.py' 2>&1", "cat 'large file.py' | less", "time cat 'large file.py'"]:
            with self.subTest(command=command):
                self.assertTrue(self.denied(command))

    def test_former_bypasses_are_closed(self):
        for command in ["cat a.py b.py",                       # two files add up
                        "cat *.py",                            # glob
                        "cd sub && cat deep.py",               # cd is followed
                        "cd sub; Get-Content deep.py",
                        "sed -n 1,201p 'large file.py'",       # sed range
                        "sed -n '5,$p' 'large file.py'",
                        "sed s/a/b/ 'large file.py'",          # sed without -n prints everything
                        "tail -n +10 'large file.py'",         # from line 10 to the end
                        "cat 'large file.py' | head -n 500"]:  # the reducer keeps too much
            with self.subTest(command=command):
                self.assertTrue(self.denied(command))

    def test_targeted_reads_and_cli_allowed(self):
        for command in ["cat small.py", "head -n 30 'large file.py'", "tail -n 30 'large file.py'",
                        "Get-Content 'large file.py' -TotalCount 20",
                        "Get-Content 'large file.py' | Select-Object -First 40",
                        "cat 'large file.py' | rg value", "rg value 'large file.py'",
                        "cat 'large file.py' | sort | uniq -c", "cat 'large file.py' > copy.py",
                        "sed -n 1,20p 'large file.py'", "sed -i s/a/b/ 'large file.py'",
                        "cat a.py", "cd sub && cat ../a.py",
                        "python kirby.py read --question test --paths 'large file.py'"]:
            with self.subTest(command=command):
                self.assertEqual(read_hook.decision(self.event(command)), {})

    def test_deny_reason_names_the_native_reader(self):
        result = read_hook.decision(self.event("cat 'large file.py'"))
        reason = result["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("kirby_luna_bulk_reader", reason)
        self.assertIn("250 lines (> 200)", reason)
        self.assertIn(json.dumps(str(self.root / "large file.py")), reason)

    def test_threshold_argument(self):
        self.assertEqual(read_hook.decision(self.event("cat 'large file.py'"), threshold=300), {})
        self.assertTrue(read_hook.decision(self.event("cat a.py"), threshold=100))

    def test_luna_and_other_events_skip(self):
        event = self.event("cat 'large file.py'")
        event["model"] = "gpt-5.6-luna"
        self.assertEqual(read_hook.decision(event), {})
        event["model"] = "gpt-6-astra"
        event["hook_event_name"] = "PostToolUse"
        self.assertEqual(read_hook.decision(event), {})

    def test_file_read_tool_and_workdir(self):
        event = self.event("cat 'large file.py'")
        event["cwd"] = str(self.root.parent)
        event["tool_input"]["workdir"] = str(self.root)
        self.assertTrue(read_hook.decision(event))
        event["cwd"] = str(self.root)
        event["tool_name"] = "Read"
        event["tool_input"] = {"file_path": "large file.py"}
        self.assertTrue(read_hook.decision(event))
        event["tool_input"]["limit"] = 20
        self.assertEqual(read_hook.decision(event), {})
        event["tool_input"]["limit"] = 220  # a window over the threshold is still a dump
        self.assertTrue(read_hook.decision(event))
        event["tool_input"] = {"file_path": "large file.py", "offset": 100}  # 151 lines remain
        self.assertEqual(read_hook.decision(event), {})

    @unittest.skipUnless(os.name == "nt", "Git Bash drive paths only exist on Windows")
    def test_git_bash_drive_paths_resolve(self):
        posix = "/" + str(self.root).replace(":", "").replace("\\", "/")
        self.assertTrue(self.denied(f"cat '{posix}/large file.py'"))

    def test_real_stdin_protocol(self):
        result = subprocess.run([sys.executable, str(SOURCE / "read_hook.py")],
                                input=json.dumps(self.event("cat 'large file.py'")),
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")


class HookInstallTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kirby-hook-config-")).resolve()
        self.hook_bytes = b"#!/usr/bin/env python3\nprint('hook')\n"

    def prepare(self, old_state=None, hook_bytes=None):
        content, entry = hook_config.prepare(self.root, old_state, hook_bytes)
        (self.root / "hooks.json").write_bytes(content)
        return entry

    def test_repeated_registration_preserves_hook_identity(self):
        entry = self.prepare(hook_bytes=self.hook_bytes)
        before = (self.root / "hooks.json").read_bytes()
        self.assertEqual(self.prepare({"hook_entry": entry}, self.hook_bytes), entry)
        self.assertEqual((self.root / "hooks.json").read_bytes(), before)

    def test_unrelated_hooks_restored_exactly(self):
        original = b'{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"echo hello"}]}]}}\n'
        (self.root / "hooks.json").write_bytes(original)
        entry = self.prepare(hook_bytes=self.hook_bytes)
        self.prepare({"hook_entry": entry, "hooks_before": original.decode("utf-8")})
        self.assertEqual((self.root / "hooks.json").read_bytes(), original)

    def test_later_hooks_survive_uninstall(self):
        entry = self.prepare(hook_bytes=self.hook_bytes)
        path = self.root / "hooks.json"
        document = json.loads(path.read_text())
        other = {"hooks": [{"type": "command", "command": "echo added"}]}
        document["hooks"]["PreToolUse"].append(other)
        path.write_text(json.dumps(document))
        self.prepare({"hook_entry": entry})
        self.assertEqual(json.loads(path.read_text()), {"hooks": {"PreToolUse": [other]}})

    def test_modified_hook_refuses_mutation(self):
        entry = self.prepare(hook_bytes=self.hook_bytes)
        path = self.root / "hooks.json"
        path.write_text(path.read_text().replace(hook_config.STATUS, "User changed"))
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            hook_config.prepare(self.root, {"hook_entry": entry}, self.hook_bytes)
        with self.assertRaises(ValueError):
            hook_config.prepare(self.root, {"hook_entry": entry})
        self.assertEqual(path.read_bytes(), before)

    def test_absent_original_hooks_leaves_valid_empty_config(self):
        entry = self.prepare(hook_bytes=self.hook_bytes)
        self.prepare({"hook_entry": entry})
        self.assertEqual(json.loads((self.root / "hooks.json").read_text()), {})

    def test_unrecorded_hook_refuses_duplicate_registration(self):
        entry = self.prepare(hook_bytes=self.hook_bytes)
        with self.assertRaises(ValueError):
            hook_config.prepare(self.root, None, self.hook_bytes)
        self.assertEqual(json.loads((self.root / "hooks.json").read_text())["hooks"]["PreToolUse"], [entry])

    def test_legacy_sidetrack_entry_counts_as_unrecorded(self):
        legacy = {"hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [
            {"type": "command", "command": "python old.py", "statusMessage": hook_config.LEGACY_STATUS}]}]}}
        (self.root / "hooks.json").write_text(json.dumps(legacy))
        with self.assertRaises(ValueError):
            hook_config.prepare(self.root, None, self.hook_bytes)
