"""Offline installer tests. Test artifacts stay in a temporary directory."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).parents[1]
sys.path.insert(0, str(SOURCE))
import hook_config

SPEC = importlib.util.spec_from_file_location("kirby_install", SOURCE / "install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kirby-test-")).resolve()

    def run_action(self, action, *args):
        self.stdout = io.StringIO()
        with contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(io.StringIO()):
            return installer.main([action, "--codex-home", str(self.root), *args])

    def test_install_preserves_config_and_auth(self):
        for name in ("config.toml", "auth.json"):
            (self.root / name).write_bytes(b"untouched")
        self.assertEqual(self.run_action("install"), 0)
        self.assertEqual(self.run_action("status"), 0)
        for name in ("config.toml", "auth.json"):
            self.assertEqual((self.root / name).read_bytes(), b"untouched")
        for name in installer.ASSETS:
            self.assertTrue((self.root / name).is_file())

    def test_reinstall_is_idempotent(self):
        self.run_action("install")
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.run_action("install"), 0)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_checkout_newlines_do_not_change_installed_assets(self):
        self.run_action("install")
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        source = Path(tempfile.mkdtemp(prefix="kirby-crlf-")).resolve()
        for name in installer.ASSETS:
            dest = source / installer.SOURCES[name]
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = (installer.SOURCE / installer.SOURCES[name]).read_bytes()
            dest.write_bytes(data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        with patch.object(installer, "SOURCE", source):
            self.assertEqual(self.run_action("install"), 0)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_allow_rule_requires_opt_in_and_survives_reinstall(self):
        self.run_action("install")
        rule = self.root / installer.RULE
        self.assertFalse(rule.exists())
        self.assertEqual(self.run_action("install", "--allow-luna"), 0)
        original = rule.read_bytes()
        pattern = json.dumps([str(Path(installer.sys.executable).resolve()),
                              str(self.root / installer.ASSETS[0])])
        self.assertIn(pattern, original.decode())
        self.assertEqual(self.run_action("install"), 0)
        self.assertEqual(rule.read_bytes(), original)
        self.assertEqual(self.run_action("status"), 0)
        self.assertEqual(self.run_action("uninstall"), 0)
        self.assertFalse(rule.exists())

    def test_allow_rule_dry_run_writes_nothing(self):
        self.assertEqual(self.run_action("install", "--allow-luna", "--dry-run"), 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unowned_rule_is_not_overwritten(self):
        rule = self.root / installer.RULE
        rule.parent.mkdir()
        rule.write_text("User rule")
        self.assertEqual(self.run_action("install", "--allow-luna"), 1)
        self.assertEqual(rule.read_text(), "User rule")
        self.assertFalse((self.root / installer.STATE).exists())

    def test_modified_rule_prevents_upgrade_and_removal(self):
        self.run_action("install", "--allow-luna")
        rule = self.root / installer.RULE
        rule.write_text("User rule")
        self.assertEqual(self.run_action("install"), 1)
        self.assertEqual(self.run_action("uninstall"), 1)
        self.assertEqual(rule.read_text(), "User rule")

    def test_uninstall_preserves_existing_instruction_bytes(self):
        original = b"# User preferences\r\n\r\nKeep my settings.\r\n"
        (self.root / "AGENTS.md").write_bytes(original)
        self.run_action("install")
        self.assertEqual(self.run_action("uninstall"), 0)
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), original)
        self.assertFalse((self.root / installer.STATE).exists())
        for name in installer.ASSETS:
            self.assertFalse((self.root / name).exists())
            self.assertTrue(list((self.root / "kirby/backups").glob(f"*/{name}")))

    def test_override_file_is_used(self):
        (self.root / "AGENTS.override.md").write_text("User override")
        self.run_action("install")
        state = json.loads((self.root / installer.STATE).read_text())
        self.assertEqual(state["instructions"], "AGENTS.override.md")
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_new_override_is_detected(self):
        self.run_action("install")
        (self.root / "AGENTS.override.md").write_text("Shadowing instructions")
        self.assertEqual(self.run_action("status"), 1)
        self.assertEqual(self.run_action("install"), 1)
        self.assertEqual(self.run_action("uninstall"), 0)
        self.assertEqual(self.run_action("install"), 0)

    def test_dry_run_writes_nothing(self):
        self.assertEqual(self.run_action("install", "--dry-run"), 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_refuses_unowned_file_without_partial_install(self):
        dest = self.root / installer.ASSETS[1]
        dest.parent.mkdir(parents=True)
        dest.write_text("User agent")
        self.assertEqual(self.run_action("install"), 1)
        self.assertFalse((self.root / installer.ASSETS[0]).exists())
        self.assertEqual(dest.read_text(), "User agent")

    def test_modified_worker_prevents_uninstall_and_upgrade(self):
        self.run_action("install")
        dest = self.root / installer.ASSETS[0]
        dest.write_text("User edits")
        self.assertEqual(self.run_action("uninstall"), 1)
        self.assertEqual(self.run_action("install"), 1)
        self.assertTrue((self.root / installer.ASSETS[1]).exists())
        self.assertEqual(dest.read_text(), "User edits")

    def test_user_text_added_after_install_survives(self):
        self.run_action("install")
        path = self.root / "AGENTS.md"
        path.write_bytes(path.read_bytes() + b"\nLater preference\n")
        self.run_action("uninstall")
        self.assertEqual(path.read_bytes(), b"\nLater preference\n")

    def test_modified_routing_prevents_uninstall(self):
        self.run_action("install")
        path = self.root / "AGENTS.md"
        path.write_text(path.read_text().replace("Keep the selected", "Keep your selected"))
        self.assertEqual(self.run_action("uninstall"), 1)
        self.assertTrue((self.root / installer.STATE).exists())

    def test_symlink_destination_rejected(self):
        outside = Path(tempfile.mkdtemp(prefix="kirby-outside-"))
        try:
            (self.root / "skills").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable on this host")
        self.assertEqual(self.run_action("install"), 1)
        self.assertEqual(list(outside.iterdir()), [])

    def test_uninstall_dry_run_preserves_files(self):
        self.run_action("install")
        self.assertEqual(self.run_action("uninstall", "--dry-run"), 0)
        self.assertEqual(self.run_action("status"), 0)

    def test_upgrade_backs_up_old_worker(self):
        self.run_action("install")
        source = Path(tempfile.mkdtemp(prefix="kirby-source-"))
        for name in installer.ASSETS:
            dest = source / installer.SOURCES[name]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((installer.SOURCE / installer.SOURCES[name]).read_bytes())
        name = installer.ASSETS[0]
        original = (self.root / name).read_bytes()
        (source / installer.SOURCES[name]).write_bytes(original + b"\n# Updated release\n")
        with patch.object(installer, "SOURCE", source):
            self.assertEqual(self.run_action("install"), 0)
        self.assertEqual((self.root / name).read_bytes(), original + b"\n# Updated release\n")
        backups = list((self.root / "kirby/backups").glob(f"*/{name}"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(self.run_action("status"), 0)

    def test_path_escape_is_rejected(self):
        with self.assertRaises(installer.InstallError):
            installer.target(self.root, "../outside.toml")

    def legacy_install(self):
        files = {name: b"legacy managed content" for name in installer.LEGACY_ASSETS}
        for name, data in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        block = installer.START + "\nOld native routing\n" + installer.END
        (self.root / "AGENTS.md").write_bytes(("User preference\n\n" + block).encode("utf-8"))
        path = self.root / installer.STATE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "instructions": "AGENTS.md", "separator": "\n\n",
                                   "block": block, "hashes": {n: installer.digest(v) for n, v in files.items()}}), encoding="utf-8")

    def test_legacy_native_install_migrates_and_backs_up_agents(self):
        self.legacy_install()
        self.assertEqual(self.run_action("install"), 0)
        self.assertEqual(json.loads((self.root / installer.STATE).read_text())["version"], 5)
        for name in installer.LEGACY_ASSETS[:2]:
            self.assertIn('model = "gpt-5.6-luna"', (self.root / name).read_text())
            self.assertTrue(list((self.root / "kirby/backups").glob(f"*/{name}")))
        self.assertIn("User preference", (self.root / "AGENTS.md").read_text())
        self.assertNotIn("Old native routing", (self.root / "AGENTS.md").read_text())
        self.assertEqual(self.run_action("uninstall"), 0)
        self.assertEqual((self.root / "AGENTS.md").read_text(), "User preference")

    def test_migration_does_not_overwrite_unowned_cli_script(self):
        self.legacy_install()
        path = self.root / installer.ASSETS[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("My script")
        self.assertEqual(self.run_action("install"), 1)
        self.assertEqual(path.read_text(), "My script")
        self.assertTrue((self.root / installer.LEGACY_ASSETS[0]).exists())

    def cli_install(self, version=3, unrelated=False):
        names = (*installer.CLI_ASSETS, installer.READ_HOOK) if version == 3 else installer.CLI_ASSETS
        files = {name: b"legacy CLI content" for name in names}
        for name, data in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        block = installer.START + "\nOld mandatory CLI routing\n" + installer.END
        (self.root / "AGENTS.md").write_bytes(("User preference\n\n" + block).encode("utf-8"))
        state = {"version": version, "instructions": "AGENTS.md", "separator": "\n\n",
                 "block": block, "hashes": {n: installer.digest(v) for n, v in files.items()}}
        if version == 3:
            entry = {"matcher": "exec_command", "hooks": [{"type": "command", "command": "python read_hook.py"}]}
            state.update(hook_entry=entry, hooks_before="{}\n")
            config = {"hooks": {"PreToolUse": [entry]}}
            if unrelated:
                config["custom"] = True
                config["hooks"]["PreToolUse"].append({"matcher": "Other", "hooks": []})
                config["hooks"]["PostToolUse"] = [{"matcher": "Other", "hooks": []}]
            (self.root / "hooks.json").write_text(json.dumps(config), encoding="utf-8")
        path = self.root / installer.STATE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")

    def test_cli_v2_migrates_to_native_routing(self):
        self.cli_install(version=2)
        self.assertEqual(self.run_action("install"), 0)
        self.assertEqual(self.run_action("status"), 0)
        self.assertEqual(json.loads((self.root / installer.STATE).read_text())["version"], 5)
        self.assertIn("native Codex Luna subagents", (self.root / "AGENTS.md").read_text())
        self.assertIn("kirby_luna_bulk_reader", (self.root / "AGENTS.md").read_text())
        self.assertNotIn("Old mandatory CLI", (self.root / "AGENTS.md").read_text())
        self.assertEqual(self.kirby_hooks(), 1)

    def kirby_hooks(self):
        config = json.loads((self.root / "hooks.json").read_text())
        return sum(1 for entry in config.get("hooks", {}).get("PreToolUse", [])
                   if any(h.get("statusMessage") == hook_config.STATUS for h in entry.get("hooks", [])))

    def test_fresh_install_registers_hook_and_uninstall_removes_the_file(self):
        self.assertEqual(self.run_action("install"), 0)
        self.assertEqual(self.kirby_hooks(), 1)
        entry = json.loads((self.root / installer.STATE).read_text())["hook_entry"]
        self.assertIn(str(self.root / installer.READ_HOOK), entry["hooks"][0]["command"])
        self.assertIn("Kirby entry", self.stdout.getvalue())
        self.assertEqual(self.run_action("status"), 0)
        self.assertIn("blocking read hook", self.stdout.getvalue())
        self.assertEqual(self.run_action("uninstall"), 0)
        self.assertFalse((self.root / "hooks.json").exists())
        self.assertTrue(list((self.root / "kirby/backups").glob("*/hooks.json")))

    def test_v3_migration_preserves_unrelated_hooks_and_backs_up(self):
        self.cli_install(unrelated=True)
        original = (self.root / "hooks.json").read_bytes()
        self.assertEqual(self.run_action("install"), 0)
        config = json.loads((self.root / "hooks.json").read_text())
        self.assertEqual(config["custom"], True)
        self.assertEqual(config["hooks"]["PostToolUse"], [{"matcher": "Other", "hooks": []}])
        self.assertEqual(config["hooks"]["PreToolUse"][0], {"matcher": "Other", "hooks": []})
        self.assertEqual(len(config["hooks"]["PreToolUse"]), 2)
        self.assertEqual(self.kirby_hooks(), 1)
        self.assertEqual((self.root / installer.READ_HOOK).read_bytes(),
                         (SOURCE / "read_hook.py").read_bytes().replace(b"\r\n", b"\n"))
        backup = next((self.root / "kirby/backups").glob("*/hooks.json"))
        self.assertEqual(backup.read_bytes(), original)
        self.assertTrue(list((self.root / "kirby/backups").glob(f"*/{installer.READ_HOOK}")))
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.run_action("install"), 0)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertEqual(self.run_action("uninstall"), 0)
        self.assertEqual(json.loads((self.root / "hooks.json").read_text()), {"custom": True, "hooks": {
            "PreToolUse": [{"matcher": "Other", "hooks": []}],
            "PostToolUse": [{"matcher": "Other", "hooks": []}]}})

    def test_v3_migration_replaces_hook_and_uninstall_restores_empty_config(self):
        self.cli_install()
        self.assertEqual(self.run_action("install"), 0)
        config = json.loads((self.root / "hooks.json").read_text())
        self.assertEqual(len(config["hooks"]["PreToolUse"]), 1)
        self.assertEqual(self.kirby_hooks(), 1)
        self.assertEqual(self.run_action("uninstall"), 0)
        self.assertEqual((self.root / "hooks.json").read_bytes(), b"{}\n")

    def test_v3_windows_routing_migrates_without_losing_user_text(self):
        self.cli_install()
        path = self.root / "AGENTS.md"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(self.run_action("install"), 0)
        self.assertTrue(path.read_bytes().startswith(b"User preference\r\n\r\n"))
        self.assertEqual(self.run_action("status"), 0)
        self.assertEqual(self.run_action("uninstall"), 0)
        self.assertEqual(path.read_bytes(), b"User preference")

    def test_v3_windows_routing_uninstall_preserves_user_text(self):
        self.cli_install()
        path = self.root / "AGENTS.md"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(self.run_action("uninstall"), 0)
        self.assertEqual(path.read_bytes(), b"User preference")

    def test_v3_uninstall_removes_only_recorded_hook(self):
        self.cli_install(unrelated=True)
        self.assertEqual(self.run_action("uninstall"), 0)
        config = json.loads((self.root / "hooks.json").read_text())
        self.assertEqual(config["hooks"]["PreToolUse"], [{"matcher": "Other", "hooks": []}])
        self.assertEqual((self.root / "AGENTS.md").read_text(), "User preference")

    def test_v3_dry_run_preserves_every_file(self):
        self.cli_install()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        for action in ("install", "uninstall"):
            self.assertEqual(self.run_action(action, "--dry-run"), 0)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_v3_changed_or_missing_hook_fails_without_writes(self):
        self.cli_install()
        for config in ({}, {"hooks": {"PreToolUse": [{"matcher": "Changed", "hooks": []}]}}):
            (self.root / "hooks.json").write_text(json.dumps(config))
            before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
            for action in ("install", "uninstall"):
                self.assertEqual(self.run_action(action), 1)
            self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_v3_modified_managed_asset_prevents_migration(self):
        self.cli_install()
        (self.root / installer.READ_HOOK).write_text("User hook changes")
        original = (self.root / "hooks.json").read_bytes()
        self.assertEqual(self.run_action("install"), 1)
        self.assertEqual((self.root / "hooks.json").read_bytes(), original)
        self.assertFalse((self.root / installer.ASSETS[2]).exists())


if __name__ == "__main__":
    unittest.main()
