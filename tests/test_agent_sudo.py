"""Exercise both Linux agents without touching the host's sudo configuration."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_agent(relative):
    spec = importlib.util.spec_from_file_location("sudo_agent", ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SudoTests:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = Path(self.directory.name)
        self.config = self.storage / "config.json"
        self.config.write_text(json.dumps({
            "server_url": "https://example.invalid", "asset_id": 1, "systempass": "secret",
        }))
        self.rules = self.storage / "sudoers.d"
        self.rules.mkdir()
        self.bootstrap = {"approved": 2, "tasks": [
            {"attribute_id": 12, "attribute_name": "aaa-first", "commands": [
                {"command": "id -u", "needs_privilege": True},
            ]},
            {"attribute_id": 5, "attribute_name": "zzz-last", "commands": [
                {"command": "echo first", "needs_privilege": True},
                {"command": "echo second", "needs_privilege": True},
                {"command": "echo first", "needs_privilege": True},
                {"command": "echo unprivileged", "needs_privilege": False},
            ]},
            {"attribute_id": 20, "attribute_name": "unprivileged", "commands": [
                {"command": "true", "needs_privilege": False},
            ]},
        ]}
        self.output = io.StringIO()
        patches = [
            patch.object(self.agent, "SUDOERS_DIR", self.rules),
            patch.object(self.agent.os, "geteuid", return_value=0),
            patch.object(self.agent, "fetch_bootstrap", return_value=(self.bootstrap, "linux", {})),
            patch.object(self.agent, "_validate_sudoers", return_value=True),
            patch.object(self.agent, "_sh_path", return_value="/bin/sh"),
            patch.object(self.agent.shutil, "which", side_effect=lambda name: "/usr/bin/" + name),
            patch.object(self.agent, "ask", side_effect=AssertionError("Non-interactive command prompted")),
            contextlib.redirect_stdout(self.output),
            contextlib.redirect_stderr(self.output),
        ]
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for item in patches:
            self.stack.enter_context(item)

    def run_cli(self, *args):
        with patch.object(sys, "argv", ["tuxcmdb-agent", "--config", str(self.config), *args]):
            return self.agent.main()

    def rule(self, name):
        return self.rules / ("tuxcmdb-agent-" + str(name))

    def test_selected_id_is_attribute_id_not_position_and_keeps_all_commands(self):
        self.assertEqual(self.run_cli("sudo", "add", "--ids", "5"), 0)
        self.assertFalse(self.rule(12).exists())
        content = self.rule(5).read_text()
        self.assertEqual(content.count("NOPASSWD:"), 2)
        self.assertIn("/usr/bin/echo first", content)
        self.assertIn("/usr/bin/echo second", content)
        self.assertNotIn("unprivileged", content)
        self.assertEqual(stat.S_IMODE(self.rule(5).stat().st_mode), 0o440)

    def test_add_all_and_comma_separated_id_alias(self):
        self.assertEqual(self.run_cli("sudo", "add", "--all"), 0)
        self.assertEqual({p.name for p in self.rules.iterdir()},
                         {"tuxcmdb-agent-5", "tuxcmdb-agent-12"})
        self.assertEqual(self.run_cli("sudo", "remove", "--id", "12, 5,12"), 0)
        self.assertEqual(list(self.rules.iterdir()), [])

    def test_remove_all_is_offline_and_leaves_other_sudo_rules(self):
        for name in ("5", "obsolete-name"):
            self.rule(name).write_text("old")
        unrelated = self.rules / "admin"
        unrelated.write_text("unrelated")
        self.config.unlink()
        self.assertEqual(self.run_cli("sudo", "remove", "--all"), 0)
        self.agent.fetch_bootstrap.assert_not_called()
        self.assertEqual(list(self.rules.iterdir()), [unrelated])

    def test_unknown_and_unprivileged_ids_fail_before_any_changes(self):
        for ids in ("5,99", "5,20", "1"):
            with self.subTest(ids=ids):
                self.assertEqual(self.run_cli("sudo", "add", "--ids", ids), 1)
                self.assertEqual(list(self.rules.iterdir()), [])
        self.assertIn("attribute ID(s)", self.output.getvalue())

    def test_malformed_and_missing_selections_are_usage_errors(self):
        arguments = [
            ("sudo", "add"), ("sudo", "remove"), ("sudo", "--all"),
            ("report", "add", "--all"), ("report", "--ids", "5"),
            ("sudo", "add", "--all", "--ids", "5"),
        ]
        arguments += [("sudo", "add", "--ids", value)
                      for value in ("", "0", "-1", "5,", "5,,12", "abc", "1.5")]
        for args in arguments:
            with self.subTest(args=args), self.assertRaises(SystemExit) as exc:
                self.run_cli(*args)
            self.assertEqual(exc.exception.code, 2)
        self.assertEqual(list(self.rules.iterdir()), [])

    def test_legacy_rule_is_migrated_on_add_and_removed_by_id(self):
        legacy = self.rule("zzz-last")
        legacy.write_text("old")
        self.assertEqual(self.run_cli("sudo", "add", "--id", "5"), 0)
        self.assertFalse(legacy.exists())
        legacy.write_text("old")
        self.assertEqual(self.run_cli("sudo", "remove", "--ids", "5"), 0)
        self.assertFalse(legacy.exists())
        self.assertFalse(self.rule(5).exists())

    def test_failed_validation_preserves_existing_and_legacy_rules(self):
        self.rule(5).write_text("existing")
        self.rule("zzz-last").write_text("legacy")
        self.agent._validate_sudoers.return_value = False
        self.assertEqual(self.run_cli("sudo", "add", "--ids", "5"), 1)
        self.assertEqual(self.rule(5).read_text(), "existing")
        self.assertEqual(self.rule("zzz-last").read_text(), "legacy")
        self.assertEqual(len(list(self.rules.iterdir())), 2)

    def test_numeric_attribute_name_does_not_remove_another_id_based_rule(self):
        self.assertEqual(self.run_cli("sudo", "add", "--id", "5"), 0)
        content = self.rule(5).read_text()
        self.bootstrap["tasks"][0]["attribute_name"] = "5"
        self.assertEqual(self.run_cli("sudo", "add", "--id", "12"), 0)
        self.assertEqual(self.rule(5).read_text(), content)
        self.assertEqual(self.run_cli("sudo", "remove", "--id", "12"), 0)
        self.assertEqual(self.rule(5).read_text(), content)

    def test_added_rule_includes_exec_tag(self):
        self.assertEqual(self.run_cli("sudo", "add", "--ids", "5"), 0)
        self.assertIn("NOPASSWD: EXEC:", self.rule(5).read_text())

    def test_listing_shows_configured_and_not_configured_status(self):
        self.assertEqual(self.run_cli("sudo", "add", "--ids", "5"), 0)
        self.agent.ask.side_effect = ["q"]
        self.assertEqual(self.run_cli("sudo"), 0)
        output = self.output.getvalue()
        self.assertIn("[configured]", output)
        self.assertIn("[not configured]", output)

    def test_stale_rule_needs_update_and_update_flag_refreshes_only_enabled_rules(self):
        self.assertEqual(self.run_cli("sudo", "add", "--ids", "5"), 0)
        fresh = self.rule(5).read_text()
        self.assertIn("EXEC:", fresh)
        # Simulate a rule installed by a pre-EXEC-tag version of the agent.
        self.rule(5).chmod(0o640)
        self.rule(5).write_text(fresh.replace("EXEC: ", ""))

        self.agent.ask.side_effect = ["q"]
        self.assertEqual(self.run_cli("sudo"), 0)
        self.assertIn("[needs update]", self.output.getvalue())

        self.assertEqual(self.run_cli("sudo", "--update"), 0)
        self.assertIn("EXEC:", self.rule(5).read_text())
        self.assertFalse(self.rule(12).exists())

    def test_update_with_nothing_enabled_is_a_noop(self):
        self.assertEqual(self.run_cli("sudo", "--update"), 0)
        self.assertIn("No enabled sudo rules to update.", self.output.getvalue())
        self.assertEqual(list(self.rules.iterdir()), [])

    def test_update_flag_validation(self):
        for args in [
            ("report", "--update"),
            ("sudo", "add", "--update", "--ids", "5"),
            ("sudo", "--update", "--all"),
            ("sudo", "--update", "--ids", "5"),
        ]:
            with self.subTest(args=args), self.assertRaises(SystemExit) as exc:
                self.run_cli(*args)
            self.assertEqual(exc.exception.code, 2)
        self.assertEqual(list(self.rules.iterdir()), [])

    def test_older_api_fails_explicitly_without_using_list_position(self):
        del self.bootstrap["tasks"][1]["attribute_id"]
        self.assertEqual(self.run_cli("sudo", "add", "--all"), 1)
        self.assertIn("upgrade the API", self.output.getvalue())
        self.assertEqual(list(self.rules.iterdir()), [])

    def test_non_root_cannot_add_or_remove(self):
        self.agent.os.geteuid.return_value = 1000
        for action in ("add", "remove"):
            self.assertEqual(self.run_cli("sudo", action, "--all"), 1)
        self.agent.fetch_bootstrap.assert_not_called()

    def test_bootstrap_error_and_network_failure_return_failure(self):
        for error in (self.agent.BootstrapError("denied"),
                      self.agent.requests.ConnectionError("unreachable")):
            with self.subTest(error=error):
                self.agent.fetch_bootstrap.side_effect = error
                self.assertEqual(self.run_cli("sudo", "add", "--all"), 1)
        self.assertEqual(list(self.rules.iterdir()), [])

    def test_interactive_menu_uses_attribute_id_and_preserves_legacy_removal(self):
        self.rule("orphan").write_text("old")
        self.agent.ask.side_effect = ["a 5", "r 5", "r orphan", "q"]
        self.assertEqual(self.run_cli("sudo"), 0)
        self.assertEqual(list(self.rules.iterdir()), [])
        self.assertIn("12. aaa-first", self.output.getvalue())
        self.assertIn(" 5. zzz-last", self.output.getvalue())

    def test_default_report_still_dispatches(self):
        with patch.object(self.agent, "cmd_report", return_value=0) as report:
            self.assertEqual(self.run_cli(), 0)
        report.assert_called_once()

    @unittest.skipUnless(shutil.which("visudo"), "visudo is not installed")
    def test_real_visudo_accepts_multi_command_rule_and_rejects_bad_rule(self):
        # Restore the real validator while leaving the host sudo directory isolated.
        original = load_agent(self.source)._validate_sudoers
        with patch.object(self.agent, "_validate_sudoers", wraps=original):
            self.assertEqual(self.run_cli("sudo", "add", "--id", "5"), 0)
            content = self.rule(5).read_text()
            self.bootstrap["tasks"][1]["commands"][0]["command"] = "echo\ninvalid sudoers line"
            self.assertEqual(self.run_cli("sudo", "add", "--id", "5"), 1)
            self.assertEqual(self.rule(5).read_text(), content)

    def test_missing_visudo_is_failure(self):
        original = load_agent(self.source)._validate_sudoers
        with patch.object(self.agent.subprocess, "run", side_effect=FileNotFoundError):
            self.assertFalse(original(self.rule(5)))
        self.assertIn("visudo is required", self.output.getvalue())


class StandardSudoTests(SudoTests, unittest.TestCase):
    source = "agent/linux/tuxcmdb_agent.py"
    agent = load_agent(source)


class EL8SudoTests(SudoTests, unittest.TestCase):
    source = "agent/linux/el8/tuxcmdb_agent.py"
    agent = load_agent(source)


if __name__ == "__main__":
    unittest.main()
