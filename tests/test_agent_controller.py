import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import HTTPServer

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agent_controller", ROOT / "deploy/openshift/agents/controller.py")
assert SPEC is not None and SPEC.loader is not None
controller = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = controller
SPEC.loader.exec_module(controller)


class AgentControllerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = Path(self.directory.name)
        self.config = controller.Config("cmdb", "https://example.test/repo.git", "main", 3600,
                                        "secret", self.storage)
        self.kube = Mock()
        self.service = controller.Controller(self.config, self.kube)
        self.commit = "a" * 40

    def release(self, version, current=False):
        path = self.storage / "releases" / version
        path.mkdir(parents=True)
        (path / ".agent-version").write_text(version)
        (path / ".agent-commit").write_text(self.commit)
        if current:
            (self.storage / "current").symlink_to(path)
        return path

    def test_default_and_persisted_retention_with_input_validation(self):
        self.assertEqual(self.service.snapshot()["versions_to_keep"], 3)
        self.service.save_settings(5)
        restored = controller.Controller(self.config, self.kube)
        self.assertEqual(restored.snapshot()["versions_to_keep"], 5)
        for value in (0, -1, True, 2.5, "3", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.save_settings(value)
        self.assertEqual(json.loads((self.storage / "settings.json").read_text())["versions_to_keep"], 5)

    def test_retention_keeps_three_distinct_versions_and_current(self):
        for number in range(5):
            path = self.release(f"0.2.26.{number}", current=number == 0)
            os.utime(path, (number + 1, number + 1))
        controller.prune_releases(self.storage, 3)
        names = {path.name for path in (self.storage / "releases").iterdir()}
        self.assertEqual(names, {"0.2.26.0", "0.2.26.4", "0.2.26.3"})
        controller.prune_releases(self.storage, 1)
        self.assertEqual([path.name for path in (self.storage / "releases").iterdir()], ["0.2.26.0"])

    def test_retention_does_not_follow_symlinks_or_delete_incomplete_builds(self):
        self.release("0.2.26.0", current=True)
        incomplete = self.storage / "releases" / "incomplete"
        incomplete.mkdir()
        outside = self.storage / "outside"
        outside.mkdir()
        (outside / ".agent-version").write_text("0.2.26.1")
        (self.storage / "releases" / "link").symlink_to(outside)
        controller.prune_releases(self.storage, 1)
        self.assertTrue(outside.is_dir())
        self.assertTrue(incomplete.is_dir())

    def test_rebuild_of_same_version_does_not_consume_a_retention_slot(self):
        first = self.release("0.2.26.0")
        repeated = self.storage / "releases" / "rebuild"
        repeated.mkdir()
        (repeated / ".agent-version").write_text("0.2.26.0")
        (self.storage / "current").symlink_to(repeated)
        older = self.release("0.2.25.0")
        controller.prune_releases(self.storage, 3)
        self.assertFalse(first.exists())
        self.assertTrue(repeated.exists())
        self.assertTrue(older.exists())

    @patch.object(controller, "remote_commit")
    def test_active_job_prevents_duplicate_build_and_pruning(self, remote):
        self.kube.request.return_value = {"items": [{"metadata": {"name": "existing"}}]}
        self.service.check(True)
        self.assertEqual(self.service.snapshot()["state"], "building")
        self.assertEqual(self.kube.request.call_count, 1)
        remote.assert_not_called()

    @patch.object(controller, "remote_commit")
    def test_unchanged_commit_is_skipped_but_manual_trigger_builds(self, remote):
        remote.return_value = self.commit
        self.release("0.2.26.0", current=True)
        self.kube.request.return_value = {"items": []}
        self.service.check(False)
        self.assertEqual(self.service.snapshot()["state"], "up-to-date")
        self.kube.request.side_effect = [
            {"items": []}, *({"image": {"dockerImageReference": f"image-{i}"}} for i in range(5)),
            {"metadata": {"name": "new-job"}},
        ]
        self.service.check(True)
        self.assertEqual(self.service.snapshot()["job"], "new-job")
        job = self.kube.request.call_args.args[1]
        self.assertEqual(job["metadata"]["annotations"]["tuxcmdb.io/agent-commit"], self.commit)
        pod = job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual([item["name"] for item in pod["initContainers"]],
                         ["checkout", "el8", "el9", "el10", "debian"])
        self.assertEqual(pod["containers"][0]["name"], "publish")
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertNotIn("serviceAccountName", pod)

    @patch.object(controller, "remote_commit")
    def test_missing_toolchain_prevents_job_creation(self, remote):
        remote.return_value = self.commit
        self.kube.request.side_effect = [{"items": []}, HTTPError("url", 404, "missing", {}, None)]
        with self.assertRaises(HTTPError) as error:
            self.service.check(True)
        error.exception.close()
        self.assertEqual(self.kube.request.call_count, 2)

    def test_remote_branch_and_annotated_tag_resolution(self):
        branch = self.storage / "repo"
        subprocess.run(["git", "init", "-b", "main", str(branch)], check=True, capture_output=True)
        for key, value in (("user.name", "Test"), ("user.email", "test@example.test")):
            subprocess.run(["git", "-C", str(branch), "config", key, value], check=True)
        subprocess.run(["git", "-C", str(branch), "commit", "--allow-empty", "-m", "init"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(branch), "tag", "-a", "v1.0.0", "-m", "release"], check=True)
        sha = subprocess.check_output(["git", "-C", str(branch), "rev-parse", "HEAD"], text=True).strip()
        for ref in ("main", "v1.0.0", "refs/tags/v1.0.0"):
            config = controller.Config("cmdb", str(branch), ref, 3600, "secret", self.storage)
            self.assertEqual(controller.remote_commit(config), sha)

    def test_http_trigger_and_settings_require_bearer_token(self):
        server = HTTPServer(("127.0.0.1", 0), controller.handler(self.service))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(url + "/build", data=b""))
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        headers = {"Authorization": "Bearer secret", "Content-Type": "application/json"}
        with urlopen(Request(url + "/build", data=b"", headers=headers)) as response:
            self.assertEqual(response.status, 202)
        self.assertTrue(self.service.wake.is_set())
        with urlopen(Request(url + "/settings", data=b'{"versions_to_keep":2}', headers=headers)) as response:
            self.assertEqual(json.load(response)["versions_to_keep"], 2)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(url + "/settings", data=b'{"versions_to_keep":0}', headers=headers))
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        self.assertEqual(self.service.keep, 2)


if __name__ == "__main__":
    unittest.main()
