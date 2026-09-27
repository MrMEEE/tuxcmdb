from __future__ import annotations

from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from .models import AssetListPreference


class AssetListFieldsTests(TestCase):
    attribute_catalog = [
        {"id": 1, "name": "cpus"},
        {"id": 2, "name": "memory_gb"},
        {"id": 3, "name": "assetname"},
    ]

    @staticmethod
    def _login(client: Client, username: str, readonly: bool = False) -> None:
        session = client.session
        session["api_username"] = username
        session["api_password"] = "test-password"
        session["api_readonly"] = readonly
        session.save()

    def _api_request(self, _username, _password, method, path, **_kwargs):
        if method == "GET" and path == "/v1/attributes":
            return self.attribute_catalog
        if method == "GET" and path == "/v1/operatingsystems":
            return []
        if method == "GET" and path == "/v1/assets":
            return [
                {
                    "id": 21,
                    "assetname": "test-host",
                    "approved": 2,
                    "active": True,
                    "attributes": [
                        {"name": "cpus", "value": "8"},
                        {"name": "memory_gb", "value": None},
                    ],
                }
            ]
        raise AssertionError(f"Unexpected API request: {method} {path}")

    def test_readonly_user_can_save_fields_and_fields_are_account_scoped(self):
        alice = Client()
        self._login(alice, "alice", readonly=True)
        bob = Client()
        self._login(bob, "bob")

        with patch("webui.views.api_request", side_effect=self._api_request), patch(
            "webui.views._get_vm_mappings", return_value={}
        ):
            response = alice.post(
                reverse("assets"),
                {"action": "save-fields", "selected_fields": ["memory_gb", "cpus", "memory_gb"]},
            )
            self.assertEqual(response.status_code, 302)

            response = bob.get(reverse("assets"))
            self.assertEqual(response.status_code, 200)

        self.assertEqual(
            AssetListPreference.objects.get(username="alice").selected_attributes,
            ["memory_gb", "cpus"],
        )
        self.assertFalse(AssetListPreference.objects.filter(username="bob").exists())
        page = response.content.decode()
        self.assertNotIn("<th>memory_gb</th>", page)

    def test_saved_fields_render_dynamic_columns_and_null_as_dash(self):
        AssetListPreference.objects.create(username="alice", selected_attributes=["memory_gb", "cpus"])
        client = Client()
        self._login(client, "alice")

        with patch("webui.views.api_request", side_effect=self._api_request), patch(
            "webui.views._get_vm_mappings", return_value={}
        ):
            response = client.get(reverse("assets"))

        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn("<th>memory_gb</th>", page)
        self.assertIn("<th>cpus</th>", page)
        self.assertIn("<td>-</td>", page)
        self.assertIn("<td>8</td>", page)

    def test_fixed_or_unknown_fields_are_rejected_without_changing_saved_preference(self):
        preference = AssetListPreference.objects.create(username="alice", selected_attributes=["cpus"])
        client = Client()
        self._login(client, "alice", readonly=True)

        with patch("webui.views.api_request", side_effect=self._api_request), patch(
            "webui.views._get_vm_mappings", return_value={}
        ):
            response = client.post(
                reverse("assets"),
                {"action": "save-fields", "selected_fields": ["approved"]},
                follow=True,
            )
        self.assertEqual(response.status_code, 200)
        preference.refresh_from_db()
        self.assertEqual(preference.selected_attributes, ["cpus"])
        self.assertContains(response, "fixed asset-list field")

        with patch("webui.views.api_request", side_effect=self._api_request), patch(
            "webui.views._get_vm_mappings", return_value={}
        ):
            response = client.post(
                reverse("assets"),
                {"action": "save-fields", "selected_fields": ["does-not-exist"]},
                follow=True,
            )
        preference.refresh_from_db()
        self.assertEqual(preference.selected_attributes, ["cpus"])
        self.assertContains(response, "Unknown attribute")
