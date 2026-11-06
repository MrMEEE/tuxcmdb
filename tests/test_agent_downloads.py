"""Public agent downloads and stable latest aliases."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tuxcmdb-webui"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tuxcmdb_webui.settings")

import django

django.setup()

from django.conf import settings
from django.test import Client, override_settings
import requests


class AgentDownloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.agents_dir = Path(self.directory.name)
        self.settings_override = override_settings(
            AGENTS_DIR=self.agents_dir, AGENT_RELEASES_DIR="",
            SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
        )
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

    def test_shared_volume_switch_updates_downloads_without_restarting_webui(self):
        first = self.agents_dir / "release-one"
        second = self.agents_dir / "release-two"
        first.mkdir()
        second.mkdir()
        first.joinpath("tuxcmdb-agent-0.2.26.4-1.el10.noarch.rpm").write_bytes(b"first")
        second.joinpath("tuxcmdb-agent-0.2.26.5-1.el10.noarch.rpm").write_bytes(b"second")
        current = self.agents_dir / "current"
        current.symlink_to(first.name)
        with override_settings(AGENTS_DIR=current):
            response = self.client.get("/agents/download/tuxcmdb-agent-latest.el10.noarch.rpm")
            self.assertEqual(response.status_code, 200)
            self.assertIn("0.2.26.4", self.client.get("/agents/").content.decode())
            next_link = self.agents_dir / "next"
            next_link.symlink_to(second.name)
            next_link.replace(current)
            self.assertEqual(b"".join(response.streaming_content), b"first")
            listing = self.client.get("/agents/").content.decode()
            self.assertIn("0.2.26.5", listing)
            self.assertNotIn("0.2.26.4", listing)
            updated = self.client.get("/agents/download/tuxcmdb-agent-latest.el10.noarch.rpm")
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(b"".join(updated.streaming_content), b"second")

    def test_latest_aliases_compare_git_versions_numerically(self):
        packages = (
            ("tuxcmdb-agent-{}-1.el10.noarch.rpm", "tuxcmdb-agent-latest.el10.noarch.rpm"),
            ("tuxcmdb-agent_{}-1.debian_all.deb", "tuxcmdb-agent_latest.debian_all.deb"),
            ("tuxcmdb-agent-{}.ps1", "tuxcmdb-agent-latest.ps1"),
        )
        for pattern, alias in packages:
            for version in ("0.2.26", "0.2.26.0", "0.2.26.9", "0.2.26.10"):
                self.add_file(pattern.format(version), version.encode())
            with self.subTest(alias=alias):
                response = self.client.get("/agents/download/" + alias)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(b"".join(response.streaming_content), b"0.2.26.10")

    def test_retained_versions_remain_downloadable_but_metadata_is_private(self):
        root = self.agents_dir / "releases"
        root.mkdir()
        for number in (4, 5):
            release = root / str(number)
            release.mkdir()
            release.joinpath(".agent-version").write_text(f"0.2.26.{number}")
            release.joinpath(f"tuxcmdb-agent-0.2.26.{number}.ps1").write_bytes(str(number).encode())
        current = self.agents_dir / "current"
        current.symlink_to(root / "5")
        with override_settings(AGENTS_DIR=current, AGENT_RELEASES_DIR=str(root)):
            listing = self.client.get("/agents/").content.decode()
            self.assertIn("0.2.26.4.ps1", listing)
            self.assertIn("0.2.26.5.ps1", listing)
            self.assertNotIn(".agent-version", listing)
            result = self.client.get("/agents/download/tuxcmdb-agent-0.2.26.4.ps1")
            self.assertEqual(b"".join(result.streaming_content), b"4")
            result = self.client.get("/agents/download/tuxcmdb-agent-latest.ps1")
            self.assertEqual(b"".join(result.streaming_content), b"5")
            self.assertEqual(self.client.get("/agents/download/.agent-version").status_code, 404)

    def login(self, client=None, readonly=False):
        client = client or self.client
        session = client.session
        session["api_username"] = "writer"
        session["api_readonly"] = readonly
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key

    @override_settings(INSTALLATION_MODE="openshift", AGENT_UPDATER_URL="http://updater:8080",
                       AGENT_UPDATER_TOKEN="secret")
    @patch("webui.agent_builds.requests.request")
    def test_build_controls_only_visible_for_writers_with_available_openshift_updater(self, request):
        request.return_value = Mock(json=Mock(return_value={"state": "up-to-date", "versions_to_keep": 3}))
        self.assertNotIn("Build agents now", self.client.get("/agents/").content.decode())
        request.assert_not_called()
        self.login(readonly=True)
        self.assertNotIn("Build agents now", self.client.get("/agents/").content.decode())
        request.assert_not_called()
        self.login()
        with override_settings(INSTALLATION_MODE="standard"):
            content = self.client.get("/agents/").content.decode()
            self.assertNotIn("Build agents now", content)
            self.assertNotIn("Agent build settings", content)
            self.assertEqual(self.client.get("/agents/settings/").status_code, 403)
            self.assertEqual(
                self.client.post("/agents/settings/", {"versions_to_keep": "5"}).status_code, 403
            )
            request.assert_not_called()
        response = self.client.get("/agents/")
        self.assertIn("Build agents now", response.content.decode())
        self.assertIn("Agent build settings", response.content.decode())
        self.assertNotIn("secret", response.content.decode())
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer secret")
        request.side_effect = requests.ConnectionError("not deployed")
        with self.assertLogs("webui.views", level="WARNING"):
            response = self.client.get("/agents/")
        self.assertNotIn("Build agents now", response.content.decode())
        self.assertIn("updater is unavailable", response.content.decode())

    @override_settings(INSTALLATION_MODE="openshift", AGENT_UPDATER_URL="http://updater:8080",
                       AGENT_UPDATER_TOKEN="secret")
    @patch("webui.agent_builds.requests.request")
    def test_build_trigger_enforces_auth_permissions_post_and_csrf(self, request):
        request.return_value = Mock(json=Mock(return_value={"state": "queued"}))
        self.assertEqual(self.client.post("/agents/build/").status_code, 302)
        self.login(readonly=True)
        self.assertEqual(self.client.post("/agents/build/").status_code, 403)
        self.login()
        self.assertEqual(self.client.get("/agents/build/").status_code, 405)
        with override_settings(INSTALLATION_MODE="standard"):
            self.assertEqual(self.client.post("/agents/build/").status_code, 403)
        request.assert_not_called()
        protected = Client(enforce_csrf_checks=True)
        self.login(protected)
        self.assertEqual(protected.post("/agents/build/").status_code, 403)
        protected.get("/agents/")
        token = protected.cookies["csrftoken"].value
        result = protected.post("/agents/build/", {"csrfmiddlewaretoken": token})
        self.assertEqual(result.status_code, 200)
        self.assertIn("Agent build requested", result.json()["message"])
        self.assertEqual(request.call_args.args[:2], ("POST", "http://updater:8080/build"))

    @override_settings(INSTALLATION_MODE="openshift", AGENT_UPDATER_URL="http://updater:8080",
                       AGENT_UPDATER_TOKEN="secret")
    @patch("webui.agent_builds.requests.request")
    def test_agent_settings_default_validation_and_save(self, request):
        request.return_value = Mock(json=Mock(return_value={"state": "idle", "versions_to_keep": 3}))
        self.login()
        response = self.client.get("/agents/settings/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/agents/?agent-settings=1")
        response = self.client.get("/agents/settings/", HTTP_ACCEPT="application/json")
        self.assertEqual(response.json()["versions_to_keep"], 3)
        self.assertEqual(response.json()["build_status"], {"state": "idle", "job": "", "error": ""})
        invalid = self.client.post("/agents/settings/", {"versions_to_keep": "0"})
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("versions_to_keep", invalid.json()["errors"])
        self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))
        result = self.client.post("/agents/settings/", {"versions_to_keep": "5"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["message"], "Agent build settings saved.")
        self.assertEqual(request.call_args.kwargs["json"], {"versions_to_keep": 5})
        self.login(readonly=True)
        self.assertEqual(self.client.post("/agents/settings/", {"versions_to_keep": "1"}).status_code, 403)

    @override_settings(INSTALLATION_MODE="openshift", AGENT_UPDATER_URL="http://updater:8080",
                       AGENT_UPDATER_TOKEN="secret")
    @patch("webui.agent_builds.requests.request")
    def test_updater_action_errors_are_json_without_redirects(self, request):
        self.login()
        request.side_effect = requests.ConnectionError("unavailable")
        for path, method in (("/agents/build/", "post"), ("/agents/settings/", "post"),
                             ("/agents/settings/", "get")):
            with self.subTest(path=path, method=method):
                result = getattr(self.client, method)(path, HTTP_ACCEPT="application/json")
                self.assertEqual(result.status_code, 502)
                self.assertIn("unavailable", result.json()["error"])
        self.login(readonly=True)
        self.assertIn("not permitted", self.client.post("/agents/build/").json()["error"])
        self.assertIn("not permitted", self.client.post("/agents/settings/").json()["error"])

    @override_settings(INSTALLATION_MODE="openshift", AGENT_UPDATER_URL="http://updater:8080",
                       AGENT_UPDATER_TOKEN="secret")
    @patch("webui.agent_builds.requests.request")
    def test_agents_page_contains_modal_controls_and_shared_script(self, request):
        request.return_value = Mock(json=Mock(return_value={"state": "idle", "versions_to_keep": 3}))
        self.login()
        content = self.client.get("/agents/").content.decode()
        for identifier in ("agent-settings-modal", "agent-install-modal",
                           "agent-feedback-modal", "agent-build-status-modal"):
            self.assertIn(f'id="{identifier}" class="tuxcmdb-modal-overlay" hidden', content)
        self.assertIn("webui/js/agents.js", content)
        self.assertNotIn('href="/agents/settings/"', content)
        with override_settings(INSTALLATION_MODE="standard"):
            content = self.client.get("/agents/").content.decode()
            self.assertNotIn('id="agent-settings-modal"', content)
            self.assertNotIn('id="agent-build-status-modal"', content)
            self.assertIn('id="agent-install-modal"', content)


if __name__ == "__main__":
    unittest.main()
