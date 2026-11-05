"""Atomic agent publication on the optional shared download volume."""

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PUBLISH = ROOT / "deploy/openshift/agents/publish"


class AgentPublishTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.source = root / "artifacts"
        self.storage = root / "shared"
        self.source.mkdir()
        self.commit = "a" * 40

    def artifacts(self, version):
        self.version = version
        self.names = [f"tuxcmdb-agent-{version}-1.el{el}.noarch.rpm" for el in (8, 9, 10)]
        self.names += [f"tuxcmdb-agent_{version}-1.debian_all.deb",
                       f"tuxcmdb-agent-{version}.ps1", "tuxcmdb-agent.ps1"]
        self.source.joinpath("agent-version").write_text(version + "\n")
        self.source.joinpath("agent-commit").write_text(self.commit + "\n")
        checksums = []
        for name in self.names:
            data = (version + name).encode()
            self.source.joinpath(name).write_bytes(data)
            checksums.append(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
        self.source.joinpath("SHA256SUMS").write_text("".join(checksums))

    def publish(self):
        return subprocess.run(
            ["bash", str(PUBLISH)], capture_output=True, text=True,
            env={**os.environ, "TUXCMDB_AGENT_SOURCE": str(self.source),
                 "TUXCMDB_AGENT_STORAGE": str(self.storage)},
        )

    def test_complete_sets_are_published_atomically_and_previous_sets_retained(self):
        self.artifacts("0.2.26.4")
        first = self.publish()
        self.assertEqual(first.returncode, 0, first.stderr)
        current = self.storage / "current"
        previous = current.resolve()
        self.assertTrue(current.is_symlink())
        self.assertEqual({file.name for file in current.iterdir()}, set(self.names))
        for name in self.names:
            self.assertEqual((current / name).read_bytes(), (self.source / name).read_bytes())
        self.artifacts("0.2.26.5")
        second = self.publish()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotEqual(current.resolve(), previous)
        self.assertTrue(previous.is_dir())
        self.assertEqual({file.name for file in current.iterdir()}, set(self.names))
        self.assertEqual(current.resolve().stat().st_mode & 0o777, 0o755)
        self.assertFalse(any(self.storage.glob(".current-*")))

    def test_corrupt_or_missing_artifacts_leave_current_unchanged(self):
        self.artifacts("0.2.26.4")
        self.assertEqual(self.publish().returncode, 0)
        previous = (self.storage / "current").resolve()
        self.artifacts("0.2.26.5")
        package = self.source / self.names[0]
        package.write_bytes(b"corrupt")
        self.assertNotEqual(self.publish().returncode, 0)
        self.assertEqual((self.storage / "current").resolve(), previous)
        package.unlink()
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing agent artifact", result.stderr)
        self.assertEqual((self.storage / "current").resolve(), previous)

    def test_invalid_metadata_fails_without_publishing(self):
        self.artifacts("0.2.26.4")
        self.source.joinpath("agent-version").write_text("../../bad")
        self.assertNotEqual(self.publish().returncode, 0)
        self.assertFalse((self.storage / "current").exists())


if __name__ == "__main__":
    unittest.main()
