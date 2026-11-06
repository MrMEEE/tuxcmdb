import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tuxcmdb-webui"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tuxcmdb_webui.settings")

import django
django.setup()

from django.test import RequestFactory
from types import SimpleNamespace
from webui.vm_matching import match_vm_asset
from webui import views


GUEST = "af4a98f1-ba57-ecdc-7ea6-b4ff1e4aacbd"
XEN = "f1984aaf-57ba-dcec-7ea6-b4ff1e4aacbd"


class VMMatchingTests(unittest.TestCase):
    def test_supports_both_byte_orders_in_either_direction(self):
        for vm, reported in ((XEN, GUEST), (GUEST, XEN), (XEN, XEN), (GUEST, GUEST)):
            with self.subTest(vm=vm, reported=reported):
                self.assertEqual(match_vm_asset(vm, {reported: "lego"}), "lego")

    def test_exact_identity_wins_over_swapped_regardless_of_order(self):
        for entries in ({GUEST: "fallback", XEN: "exact"}, {XEN: "exact", GUEST: "fallback"}):
            self.assertEqual(match_vm_asset(XEN, entries), "exact")

    def test_uuid_case_and_format_are_normalized(self):
        self.assertEqual(match_vm_asset(" " + XEN.upper() + " ", {GUEST.upper(): "lego"}), "lego")
        self.assertEqual(match_vm_asset(XEN, {XEN.replace("-", ""): "lego"}), "lego")

    def test_invalid_missing_and_non_uuid_identifiers(self):
        for value in (None, 42, "", " ", "different", "af4a98f1-ba57-ecdc-7ea6-b4ff1e4aacbe"):
            self.assertIsNone(match_vm_asset(value, {GUEST: "lego"}))
        self.assertEqual(match_vm_asset("opaque-id", {"opaque-id": "existing"}), "existing")
        self.assertIsNone(match_vm_asset(XEN, {"invalid": "asset"}))

    def test_ambiguous_normalized_or_swapped_matches_are_not_linked(self):
        for entries in ({XEN: "one", XEN.upper(): "two"},
                        {GUEST: "one", GUEST.upper(): "two"}):
            with self.assertLogs("webui.vm_matching", level="WARNING"):
                self.assertIsNone(match_vm_asset(XEN, entries))

    def test_both_vm_views_link_lego_without_changing_reported_uuid(self):
        for view, path, args in (
            (views.hypervisor_detail_view, "/hypervisors/1/", (1,)),
            (views.virtual_machines_view, "/virtual-machines/", ()),
        ):
            for reported in (GUEST, XEN):
                with self.subTest(view=view.__name__, reported=reported):
                    request = RequestFactory().get(path)
                    request.user = SimpleNamespace(is_authenticated=True, readonly=False)
                    request.session = {"api_username": "writer", "api_password": "test"}
                    cluster = {"id": 1, "name": "Xen", "attribute_name": "uuid",
                               "stats": {"vms": [{"uuid": XEN, "name": "lego.outerrim.lan"}]}}
                    assets = [{"id": 7, "assetname": "lego", "attributes": [
                        {"name": "uuid", "value": reported}]}]
                    with patch.object(views, "get_hypervisor_cluster", return_value=cluster), \
                            patch.object(views, "list_hypervisor_clusters", return_value=[cluster]), \
                            patch.object(views, "api_request", side_effect=lambda *a, **k:
                                         [cluster] if a[3] == "/v1/hypervisors" else assets), \
                            patch.object(views, "render") as render:
                        view(request, *args)
                    context = render.call_args.args[2]
                    vm = context["vms"][0]
                    self.assertEqual(vm["asset_id"], 7)
                    self.assertEqual(vm["asset_name"], "lego")
                    self.assertEqual(vm["uuid"], XEN)
                    self.assertEqual(assets[0]["attributes"][0]["value"], reported)
