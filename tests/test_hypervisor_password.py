"""Password retention across the hypervisor edit form and API."""

import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from werkzeug.security import generate_password_hash
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tuxcmdb-api"), str(ROOT / "tuxcmdb-webui"), str(ROOT)]
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tuxcmdb_webui.settings")

import django
django.setup()

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from webui.auth import SessionUser
from webui.forms import HypervisorClusterForm
from webui.views import hypervisor_form_view

api = importlib.import_module("app")


class HypervisorPasswordApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        url = f"sqlite+pysqlite:///{directory.name}/test.db"
        self.engine = api.create_db_engine(url)
        self.addCleanup(self.engine.dispose)
        api.metadata.create_all(self.engine)
        config = Path(directory.name) / "api.yaml"
        config.write_text(yaml.safe_dump({"api": {"database_url": url}}))
        self.client = TestClient(api.create_app(config))
        self.addCleanup(self.client.close)
        self.data = {
            "name": "cluster", "cluster_type": "vmware",
            "management_hostname": "hypervisor.example.test", "username": "operator",
            "verify_ssl": True,
        }
        with self.engine.begin() as conn:
            conn.execute(api.apiusers.insert().values(
                username="admin", password_hash=generate_password_hash("secret"), is_active=True,
            ))
            self.cluster_id = conn.execute(api.hypervisor_clusters.insert().values(
                **self.data, password="original-password",
            )).inserted_primary_key[0]
        self.client.auth = ("admin", "secret")
        probe_patch = patch.object(api, "probe_hypervisor_connection", return_value={"status": "ok"})
        self.probe = probe_patch.start()
        self.addCleanup(probe_patch.stop)

    def stored_password(self):
        with self.engine.connect() as conn:
            return conn.execute(api.select(api.hypervisor_clusters.c.password).where(
                api.hypervisor_clusters.c.id == self.cluster_id,
            )).scalar_one()

    def test_omitted_empty_and_null_password_reuse_existing_for_probe_and_storage(self):
        for password in ({}, {"password": ""}, {"password": None}):
            with self.subTest(password=password):
                response = self.client.patch(f"/v1/hypervisors/{self.cluster_id}", json={
                    **self.data, "name": "renamed", **password,
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.probe.call_args.kwargs["password"], "original-password")
                self.assertEqual(self.stored_password(), "original-password")
                self.assertNotIn("password", response.json())
                self.assertEqual(response.json()["name"], "renamed")

    def test_replacement_is_probed_and_saved(self):
        response = self.client.patch(f"/v1/hypervisors/{self.cluster_id}", json={
            **self.data, "password": "replacement",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.probe.call_args.kwargs["password"], "replacement")
        self.assertEqual(self.stored_password(), "replacement")

    def test_failed_probe_preserves_existing_password(self):
        self.probe.side_effect = api.HypervisorProbeError("Connection failed")
        response = self.client.patch(f"/v1/hypervisors/{self.cluster_id}", json={
            **self.data, "password": "replacement",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.stored_password(), "original-password")

    def test_missing_cluster_returns_404_without_probe(self):
        response = self.client.patch("/v1/hypervisors/99999", json=self.data)
        self.assertEqual(response.status_code, 404)
        self.probe.assert_not_called()

    def test_creation_still_requires_password(self):
        for password in ({}, {"password": ""}, {"password": None}):
            response = self.client.post("/v1/hypervisors", json={**self.data, **password})
            self.assertEqual(response.status_code, 422)
        self.probe.assert_not_called()


class HypervisorPasswordFormTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "name": "cluster", "cluster_type": "vmware", "management_hostname": "hv.example.test",
            "username": "operator", "password": "", "verify_ssl": "on",
        }

    def test_only_edit_forms_allow_blank_password(self):
        self.assertFalse(HypervisorClusterForm(self.data).is_valid())
        form = HypervisorClusterForm(self.data, require_password=False)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn("Leave blank", form.fields["password"].help_text)

    def request(self, method="post", data=None):
        factory = RequestFactory()
        request = getattr(factory, method)("/hypervisors/1/edit/", data or self.data)
        request.session = {"api_username": "admin", "api_password": "secret"}
        request.user = SessionUser(username="admin", readonly=False)
        request._messages = FallbackStorage(request)
        return request

    def test_edit_omits_blank_password_and_sends_replacement(self):
        for password in ("", "replacement"):
            with (
                self.subTest(password=password),
                patch("webui.views.api_request", return_value=[]),
                patch("webui.views.get_hypervisor_cluster", return_value=self.data),
                patch("webui.views.update_hypervisor_cluster") as update,
            ):
                response = hypervisor_form_view(self.request(data={**self.data, "password": password}), 1)
                self.assertEqual(response.status_code, 302)
                payload = update.call_args.args[3]
                if password:
                    self.assertEqual(payload["password"], password)
                else:
                    self.assertNotIn("password", payload)

    def test_edit_page_displays_retention_hint_without_password(self):
        with (
            patch("webui.views.api_request", return_value=[]),
            patch("webui.views.get_hypervisor_cluster", return_value=self.data),
        ):
            response = hypervisor_form_view(self.request(method="get"), 1)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Leave blank to keep the existing password.", response.content.decode())


if __name__ == "__main__":
    unittest.main()
