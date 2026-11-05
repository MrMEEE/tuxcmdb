"""Git-derived OpenShift package versions, including shallow build clones."""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "openshift-agent-version.sh"


class AgentVersionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.remote = Path(self.directory.name) / "remote"
        self.remote.mkdir()
        self.git("init", "--initial-branch=main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.git("commit", "--allow-empty", "-m", "initial")

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.remote), *args], text=True, stderr=subprocess.PIPE
        ).strip()

    def version(self, *, shallow=False):
        clone = Path(self.directory.name) / "clone"
        command = ["git", "clone"]
        if shallow:
            command += ["--depth=1", "--no-tags"]
        command += [self.remote.as_uri(), str(clone)]
        subprocess.run(command, check=True, capture_output=True)
        return subprocess.run(
            ["bash", str(SCRIPT)], cwd=clone, text=True, capture_output=True
        )

    def test_exact_release_appends_zero(self):
        self.git("tag", "v0.2.26")
        result = self.version()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0.2.26.0")

    def test_shallow_clone_fetches_history_and_counts_commits(self):
        self.git("tag", "v0.2.26")
        for index in range(3):
            self.git("commit", "--allow-empty", "-m", f"change {index}")
        result = self.version(shallow=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0.2.26.3")

    def test_new_release_resets_counter_and_prerelease_tags_are_ignored(self):
        self.git("tag", "v0.2.25")
        self.git("commit", "--allow-empty", "-m", "release")
        self.git("tag", "v0.2.26")
        self.git("commit", "--allow-empty", "-m", "next change")
        self.git("tag", "v0.3.0-rc1")
        result = self.version()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0.2.26.1")

    def test_unreachable_release_is_not_selected(self):
        self.git("tag", "v0.2.26")
        self.git("checkout", "-b", "future")
        self.git("commit", "--allow-empty", "-m", "future")
        self.git("tag", "v99.0.0")
        self.git("checkout", "main")
        self.git("commit", "--allow-empty", "-m", "main change")
        result = self.version()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0.2.26.1")

    def test_missing_release_fails_instead_of_using_source_version(self):
        result = self.version()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No reachable release tag", result.stderr)


if __name__ == "__main__":
    unittest.main()
