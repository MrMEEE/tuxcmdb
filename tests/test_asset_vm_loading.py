"""Asset rendering must not wait for live hypervisor probes."""

import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tuxcmdb-webui"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tuxcmdb_webui.settings")

import django
django.setup()

from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, override_settings
from webui import views
from webui.auth import SessionUser
from webui.services import ServiceError


@override_settings(CACHES={"default": {
    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    "LOCATION": "asset-vm-loading-tests",
}})
class AssetVMLoadingTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.request = RequestFactory().get("/assets/vm-mappings/")
        self.request.user = SessionUser("writer", False)
        self.request.session = {"api_username": "writer", "api_password": "secret"}
        self.assets = [{"id": 7, "assetname": "lego", "active": True, "approved": 0,
                        "attributes": [{"name": "uuid", "value": "af4a98f1-ba57-ecdc-7ea6-b4ff1e4aacbd"}]}]
        self.cluster = {"id": 1, "attribute_name": "uuid", "stats": {"vms": [
            {"uuid": "f1984aaf-57ba-dcec-7ea6-b4ff1e4aacbd"}]}}

    def api(self, username, password, method, path, **kwargs):
        if path == "/v1/assets":
            return self.assets
        if path == "/v1/hypervisors":
            return [{"id": 1}, {"id": 2}]
        return []

    def result(self):
        response = views.asset_vm_mappings_view(self.request)
        return response, json.loads(response.content)

    def test_inventory_render_does_not_call_hypervisors(self):
        with patch.object(views, "api_request", side_effect=self.api) as api, \
                patch.object(views, "_get_vm_mappings", side_effect=AssertionError("Must not block rendering")), \
                patch.object(views, "get_hypervisor_cluster", side_effect=AssertionError("Must not probe")), \
                patch.object(views.AssetListPreference.objects, "filter") as preferences:
            preferences.return_value.first.return_value = None
            response = views.assets_view(self.request)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-vm-asset-id="7"', response.content)
        self.assertIn(b"Loading", response.content)
        self.assertEqual([call.args[3] for call in api.call_args_list],
                         ["/v1/attributes", "/v1/operatingsystems", "/v1/assets/merge-candidates", "/v1/assets"])

    def test_one_asset_fetch_for_multiple_clusters_and_cache_hit(self):
        with patch.object(views, "api_request", side_effect=self.api) as api, \
                patch.object(views, "get_hypervisor_cluster", return_value=self.cluster) as probe:
            response, data = self.result()
            self.assertEqual(data["mapped_assets"], {"7": True})
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(probe.call_count, 2)
            self.assertEqual(sum(call.args[3] == "/v1/assets" for call in api.call_args_list), 1)
            api.reset_mock()
            self.assertEqual(self.result()[1], data)
            self.assertEqual(probe.call_count, 2)
            self.assertEqual([call.args[3] for call in api.call_args_list], ["/ok", "/v1/assets"])

    def test_positive_cache_survives_sixty_seconds_and_expires_after_one_hour(self):
        now = time.time()
        with patch.object(views, "api_request", side_effect=self.api), \
                patch.object(views, "get_hypervisor_cluster", return_value=self.cluster) as probe:
            self.result()
            with patch("time.time", return_value=now + 61):
                self.result()
            self.assertEqual(probe.call_count, 2)
            with patch("time.time", return_value=now + 3601):
                self.result()
            self.assertEqual(probe.call_count, 4)

    def test_mixed_results_have_independent_positive_and_negative_expiries(self):
        unmapped = {"id": 8, "assetname": "new-host", "attributes": []}
        self.assets.append(unmapped)
        now = time.time()
        with patch.object(views, "api_request", side_effect=self.api), \
                patch.object(views, "_get_vm_mappings",
                             return_value={"af4a98f1-ba57-ecdc-7ea6-b4ff1e4aacbd": ("lego", 1)}) as mappings:
            self.assertEqual(self.result()[1]["mapped_assets"], {"7": True, "8": False})
            self.result()
            self.assertEqual(mappings.call_count, 1)
            with patch("time.time", return_value=now + 61):
                self.result()
            self.assertEqual(mappings.call_count, 2)
            self.assertEqual(mappings.call_args.kwargs["asset_rows"], [unmapped])
            with patch("time.time", return_value=now + 3601):
                self.result()
            self.assertEqual(mappings.call_args.kwargs["asset_rows"], self.assets)

    def test_cache_is_scoped_to_credentials_api_and_permissions(self):
        with patch.object(views, "api_request", side_effect=self.api), \
                patch.object(views, "get_hypervisor_cluster", return_value=self.cluster) as probe:
            self.result()
            self.request.session["api_password"] = "changed"
            self.result()
            self.request.session["api_username"] = "other"
            self.result()
            self.request.user.readonly = True
            self.result()
            with patch.object(views, "api_base_url", return_value="http://different-api"):
                self.result()
            self.assertEqual(probe.call_count, 10)

    def test_failures_are_explicit_and_never_cached(self):
        with patch.object(views, "api_request", side_effect=self.api), \
                patch.object(views, "get_hypervisor_cluster", side_effect=ServiceError("Probe failed")) as probe:
            for _ in range(2):
                with self.assertLogs("webui.views", level="WARNING"):
                    response, data = self.result()
                self.assertEqual(response.status_code, 502)
                self.assertEqual(data["error"], "Probe failed")
            self.assertEqual(probe.call_count, 2)

    def test_unreachable_hypervisor_does_not_cache_empty_mapping(self):
        cluster = {**self.cluster, "stats": {"status": "unreachable", "vms": []}}
        with patch.object(views, "api_request", side_effect=self.api), \
                patch.object(views, "get_hypervisor_cluster", return_value=cluster), \
                self.assertLogs("webui.views", level="WARNING"):
            self.assertEqual(self.result()[0].status_code, 502)

    def test_cache_hit_still_checks_api_access(self):
        with patch.object(views, "api_request", side_effect=self.api), \
                patch.object(views, "get_hypervisor_cluster", return_value=self.cluster):
            self.result()
        with patch.object(views, "api_request", side_effect=ServiceError("Access revoked")), \
                self.assertLogs("webui.views", level="WARNING"):
            response, data = self.result()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("mapped_assets", data)

    def test_authentication_and_get_required(self):
        self.request.user.is_authenticated = False
        self.assertEqual(views.asset_vm_mappings_view(self.request).status_code, 302)
        self.request.user.is_authenticated = True
        self.request.method = "POST"
        self.assertEqual(views.asset_vm_mappings_view(self.request).status_code, 405)

    def test_expired_api_credentials_return_explicit_json_error(self):
        self.request.session.pop("api_password")
        with self.assertLogs("webui.views", level="WARNING"):
            response, data = self.result()
        self.assertEqual(response.status_code, 502)
        self.assertIn("expired", data["error"])

    def test_paginates_mapping_inventory(self):
        batch = [self.assets[0]] * 1000
        with patch.object(views, "api_request", side_effect=lambda *a, **k:
                          (batch if k["params"]["offset"] == 0 else self.assets)
                          if a[3] == "/v1/assets" else []), \
                patch.object(views, "_get_vm_mappings", return_value={}) as mappings:
            self.assertEqual(self.result()[0].status_code, 200)
            self.assertEqual(len(mappings.call_args.kwargs["asset_rows"]), 1001)


if __name__ == "__main__":
    unittest.main()
