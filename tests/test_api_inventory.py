from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from werkzeug.security import generate_password_hash
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tuxcmdb-api"))
sys.path.insert(0, str(ROOT))
api = importlib.import_module("app")


class InventoryApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.database_path = Path(cls.temp_dir.name) / "test.db"
        cls.database_url = f"sqlite+pysqlite:///{cls.database_path}"
        cls.config_path = Path(cls.temp_dir.name) / "api.yaml"
        cls.config_path.write_text(
            yaml.safe_dump({"api": {"database_url": cls.database_url}}),
            encoding="utf-8",
        )

        alembic_config = Config(str(ROOT / "alembic.ini"))
        alembic_config.set_main_option("script_location", str(ROOT / "alembic"))
        previous_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = cls.database_url
        try:
            command.upgrade(alembic_config, "head")
        finally:
            if previous_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_url

        engine = api.create_db_engine(cls.database_url)
        api.metadata.create_all(engine, tables=[api.apiusers])
        with engine.begin() as conn:
            conn.execute(
                api.apiusers.insert().values(
                    username="admin",
                    password_hash=generate_password_hash("secret"),
                    is_active=True,
                )
            )
            os_id = conn.execute(
                api.select(api.attributes.c.id).where(api.attributes.c.name == "os")
            ).scalar_one()
            conn.execute(
                api.attributes.update()
                .where(api.attributes.c.id == os_id)
                .values(allow_multiple=True, inventory_group=True, immutable=False)
            )
            ip_id = conn.execute(
                api.attributes.insert().values(
                    name="ip_address",
                    data_type="string",
                    allow_multiple=True,
                    inventory_group=False,
                )
            ).inserted_primary_key[0]

            fixtures = {
                "srv-rhel8": (["RHEL8"], ["192.168.1.10", "10.0.0.10"], True),
                "srv-rhel9": (["RHEL9"], ["192.168.1.11"], True),
                "srv-no-ip": (["RHEL10"], ["10.0.0.12"], True),
                "srv-ubuntu": (["Ubuntu"], ["192.168.1.13"], True),
                "srv-mixed": (["RHEL8", "RHEL9"], ["192.168.1.14"], True),
                "srv-retired": (["RHEL8"], ["192.168.1.15"], False),
            }
            for assetname, (operating_systems, addresses, active) in fixtures.items():
                asset_id = conn.execute(
                    api.assets.insert().values(assetname=assetname, active=active)
                ).inserted_primary_key[0]
                conn.execute(
                    api.assignments.insert(),
                    [
                        {
                            "asset_id": asset_id,
                            "attribute_id": os_id,
                            "value": value,
                            "assigned": True,
                        }
                        for value in operating_systems
                    ]
                    + [
                        {
                            "asset_id": asset_id,
                            "attribute_id": ip_id,
                            "value": value,
                            "assigned": True,
                        }
                        for value in addresses
                    ],
                )

        cls.client = TestClient(api.create_app(cls.config_path))
        cls.auth = ("admin", "secret")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_requested_filter_matches_same_host_in_both_endpoints(self) -> None:
        expression = "os=RHEL AND (os NOT RHEL9) AND ip_address=192.168."
        assets_response = self.client.get(
            "/v1/assets",
            params={"filter": expression, "active": "true"},
            auth=self.auth,
        )
        inventory_response = self.client.get(
            "/v1/inventory",
            params={"filter": expression},
            auth=self.auth,
        )

        self.assertEqual(assets_response.status_code, 200)
        self.assertEqual(inventory_response.status_code, 200)
        self.assertEqual([asset["assetname"] for asset in assets_response.json()], ["srv-rhel8"])
        self.assertEqual(inventory_response.json()["all"]["hosts"], ["srv-rhel8"])

    def test_inventory_contains_metadata_lists_and_groups(self) -> None:
        response = self.client.get(
            "/v1/inventory",
            params={"filter": "hostname=srv-rhel8"},
            auth=self.auth,
        )

        self.assertEqual(response.status_code, 200)
        inventory = response.json()
        hostvars = inventory["_meta"]["hostvars"]["srv-rhel8"]
        self.assertEqual(hostvars["os"], ["RHEL8"])
        self.assertEqual(hostvars["ip_address"], ["192.168.1.10", "10.0.0.10"])
        self.assertTrue(hostvars["tuxcmdb_active"])
        self.assertIn("tuxcmdb_id", hostvars)
        self.assertEqual(inventory["rhel8"]["hosts"], ["srv-rhel8"])
        self.assertEqual(inventory["all"]["children"], ["rhel8"])

    def test_prefix_is_literal_and_case_insensitive(self) -> None:
        response = self.client.get(
            "/v1/assets",
            params={"filter": "os=rhel AND ip_address=192.168.", "active": "true"},
            auth=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [asset["assetname"] for asset in response.json()],
            ["srv-mixed", "srv-rhel8", "srv-rhel9"],
        )

    def test_inventory_defaults_to_active_assets_and_requires_auth(self) -> None:
        response = self.client.get("/v1/inventory", auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("srv-retired", response.json()["all"]["hosts"])
        self.assertEqual(self.client.get("/v1/inventory").status_code, 401)

    def test_assetname_and_active_filters_support_gui_queries(self) -> None:
        response = self.client.get(
            "/v1/assets",
            params={"filter": "active=false AND assetname=srv-ret"},
            auth=self.auth,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([asset["assetname"] for asset in response.json()], ["srv-retired"])

    def test_invalid_filter_returns_bad_request(self) -> None:
        response = self.client.get(
            "/v1/assets",
            params={"filter": "os="},
            auth=self.auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("position", response.json()["detail"])

    def test_inventory_group_is_exposed_by_attribute_crud(self) -> None:
        created = self.client.post(
            "/v1/attributes",
            json={"name": "site", "inventory_group": True},
            auth=self.auth,
        )
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.json()["inventory_group"])

        updated = self.client.patch(
            f"/v1/attributes/{created.json()['id']}",
            json={"inventory_group": False},
            auth=self.auth,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertFalse(updated.json()["inventory_group"])

    def test_group_names_are_safe_and_reserved_names_do_not_collide(self) -> None:
        self.assertEqual(api.sanitize_inventory_group("Production / West"), "production_west")
        self.assertEqual(api.sanitize_inventory_group("42"), "_42")
        self.assertEqual(api.sanitize_inventory_group("all"), "_all")
        self.assertIsNone(api.sanitize_inventory_group("---"))


if __name__ == "__main__":
    unittest.main()