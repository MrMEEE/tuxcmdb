"""Public agent downloads and stable latest aliases."""

import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tuxcmdb-webui"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tuxcmdb_webui.settings")

import django

django.setup()

from django.test import Client, override_settings


class AgentDownloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.agents_dir = Path(self.directory.name)
        self.settings_override = override_settings(AGENTS_DIR=self.agents_dir)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.client = Client()

    def add_file(self, name, content):
        (self.agents_dir / name).write_bytes(content)

    def test_versioned_and_latest_rpm_are_downloads(self):
        self.add_file("tuxcmdb-agent-0.2.9-1.el10.noarch.rpm", b"older")
        self.add_file("tuxcmdb-agent-0.2.22-1.el10.noarch.rpm", b"newest")
        self.add_file("tuxcmdb-agent-0.3.0-1.el9.noarch.rpm", b"other platform")
        self.add_file("tuxcmdb-agent-0.2.22-1.el8.noarch.rpm", b"el8")
        for filename, expected in (
            ("tuxcmdb-agent-0.2.9-1.el10.noarch.rpm", b"older"),
            ("tuxcmdb-agent-latest.el10.noarch.rpm", b"newest"),
            ("tuxcmdb-agent-latest.el9.noarch.rpm", b"other platform"),
            ("tuxcmdb-agent-latest.el8.noarch.rpm", b"el8"),
        ):
            with self.subTest(filename=filename):
                response = self.client.get("/agents/download/" + filename)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(b"".join(response.streaming_content), expected)
                self.assertEqual(response["Content-Type"], "application/octet-stream")
                self.assertEqual(response["Content-Disposition"], f'attachment; filename="{filename}"')
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")
                if "latest" in filename:
                    self.assertEqual(response["Cache-Control"], "no-store")

    def test_deb_and_windows_latest_are_listed_and_resolved(self):
        self.add_file("tuxcmdb-agent_0.2.22-1.debian_all.deb", b"deb")
        self.add_file("tuxcmdb-agent-0.2.9.ps1", b"old powershell")
        self.add_file("tuxcmdb-agent-0.2.22.ps1", b"powershell")
        self.add_file("tuxcmdb-agent.ps1", b"legacy")
        response = self.client.get("/agents/")
        self.assertEqual(response.status_code, 200)
        for alias, expected in (
            ("tuxcmdb-agent_latest.debian_all.deb", b"deb"),
            ("tuxcmdb-agent-latest.ps1", b"powershell"),
        ):
            with self.subTest(alias=alias):
                self.assertIn(f'download="{alias}"', response.content.decode())
                result = self.client.get("/agents/download/" + alias)
                self.assertEqual(b"".join(result.streaming_content), expected)
                self.assertEqual(result["Content-Disposition"], f'attachment; filename="{alias}"')

    def test_legacy_windows_package_has_latest_alias(self):
        self.add_file("tuxcmdb-agent.ps1", b"legacy")
        response = self.client.get("/agents/download/tuxcmdb-agent-latest.ps1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"legacy")

    def test_latest_alias_disappears_when_no_matching_file_exists(self):
        response = self.client.get("/agents/download/tuxcmdb-agent-latest.el10.noarch.rpm")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("tuxcmdb-agent-latest.el10.noarch.rpm", self.client.get("/agents/").content.decode())


if __name__ == "__main__":
    unittest.main()
