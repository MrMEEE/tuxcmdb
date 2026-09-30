"""Rendering coverage for the conditional last-checkin asset column."""

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tuxcmdb-webui"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tuxcmdb_webui.settings")

import django

django.setup()

from django.test import RequestFactory
from webui.auth import SessionUser
from webui.views import asset_detail_view, assets_view


class AssetListCheckinTests(unittest.TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/assets/")
        self.request.session = {"api_username": "admin", "api_password": "secret"}
        self.request.user = SessionUser(username="admin", readonly=False)

    def render_assets(self, assets):
        def api_response(_username, _password, _method, path, **_kwargs):
            if path == "/v1/assets":
                return assets
            return []

        with (
            patch("webui.views.api_request", side_effect=api_response),
            patch("webui.views._get_vm_mappings", return_value={}),
            patch("webui.views.AssetListPreference.objects.filter") as preferences,
        ):
            preferences.return_value.first.return_value = None
            return assets_view(self.request).content.decode()

    @staticmethod
    def asset(name, checkin=None):
        return {
            "id": 1,
            "assetname": name,
            "active": True,
            "approved": 0,
            "attributes": [],
            "last_checkin_at": checkin,
        }

    def test_shows_checkin_column_when_any_asset_has_checkin(self):
        html = self.render_assets([
            self.asset("checked-in", "2026-09-30T12:34:56Z"),
            self.asset("not-checked-in"),
        ])
        self.assertIn("<th>Last checkin</th>", html)
        self.assertIn("2026-09-30T12:34:56Z", html)
        self.assertIn("<td>Never</td>", html)

    def test_hides_checkin_column_when_no_asset_has_checkin(self):
        html = self.render_assets([self.asset("not-checked-in")])
        self.assertNotIn("<th>Last checkin</th>", html)
        self.assertNotIn("<td>Never</td>", html)

    def test_empty_list_keeps_six_column_layout(self):
        html = self.render_assets([])
        self.assertNotIn("<th>Last checkin</th>", html)
        self.assertIn('<td colspan="6">No assets found.</td>', html)

    def test_shows_agent_version_only_when_reported(self):
        reported = self.asset("reported")
        reported["attributes"] = [{"name": "tuxcmdb-agent-version", "value": "0.2.18"}]
        html = self.render_assets([reported, self.asset("manual")])
        self.assertIn("<th>Agent version</th>", html)
        self.assertIn("<td>0.2.18</td>", html)
        self.assertIn("<td>-</td>", html)

        html = self.render_assets([self.asset("manual")])
        self.assertNotIn("<th>Agent version</th>", html)

    def test_asset_detail_shows_reported_version(self):
        asset = self.asset("reported")
        asset["attributes"] = [{"name": "tuxcmdb-agent-version", "value": "0.2.18"}]

        def api_response(_username, _password, _method, path, **_kwargs):
            if path == "/v1/assets":
                return [asset]
            if path == "/v1/assets/1":
                return asset
            return []

        with (
            patch("webui.views.api_request", side_effect=api_response),
            patch("webui.views._get_vm_mappings", return_value={}),
        ):
            html = asset_detail_view(self.request, "reported").content.decode()
        self.assertIn("<label>Agent version</label>", html)
        self.assertIn("<div>0.2.18</div>", html)

    def test_same_named_assets_link_to_distinct_detail_pages(self):
        manual = self.asset("shared-host")
        agent = {**self.asset("shared-host"), "id": 2, "approved": 1}
        html = self.render_assets([manual, agent])
        self.assertIn('href="/assets/1/"', html)
        self.assertIn('href="/assets/2/"', html)

        def api_response(_username, _password, _method, path, **_kwargs):
            if path == "/v1/assets/2":
                return agent
            if path == "/v1/assets":
                return [manual, agent]
            return []

        request = RequestFactory().get("/assets/2/")
        request.session = self.request.session
        request.user = self.request.user
        with (
            patch("webui.views.api_request", side_effect=api_response),
            patch("webui.views._get_vm_mappings", return_value={}),
        ):
            detail = asset_detail_view(request, "2")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Pending", detail.content.decode())


if __name__ == "__main__":
    unittest.main()
