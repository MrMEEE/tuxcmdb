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


class AssetMergeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.db"
        self.database_url = f"sqlite+pysqlite:///{self.database_path}"
        self.config_path = Path(self.temp_dir.name) / "api.yaml"
        self.config_path.write_text(
            yaml.safe_dump({"api": {"database_url": self.database_url}}),
            encoding="utf-8",
        )

        alembic_config = Config(str(ROOT / "alembic.ini"))
        alembic_config.set_main_option("script_location", str(ROOT / "alembic"))
        previous_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = self.database_url
        try:
            command.upgrade(alembic_config, "head")
        finally:
            if previous_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_url

        self.engine = api.create_db_engine(self.database_url)
        with self.engine.begin() as conn:
            conn.execute(
                api.apiusers.insert().values(
                    username="admin",
                    password_hash=generate_password_hash("secret"),
                    is_active=True,
                    readonly=False,
                )
            )
            conn.execute(
                api.apiusers.insert().values(
                    username="reader",
                    password_hash=generate_password_hash("secret"),
                    is_active=True,
                    readonly=True,
                )
            )
            self.location_id = conn.execute(
                api.attributes.insert().values(
                    name="location",
                    data_type="string",
                    allow_multiple=False,
                    immutable=False,
                    inventory_group=False,
                )
            ).inserted_primary_key[0]
            self.owner_id = conn.execute(
                api.attributes.insert().values(
                    name="owner",
                    data_type="string",
                    allow_multiple=False,
                    immutable=False,
                    inventory_group=False,
                )
            ).inserted_primary_key[0]
            self.tags_id = conn.execute(
                api.attributes.insert().values(
                    name="tags",
                    data_type="string",
                    allow_multiple=True,
                    immutable=False,
                    inventory_group=False,
                )
            ).inserted_primary_key[0]
            self.os_id = conn.execute(
                api.operatingsystems.insert().values(name="RHEL 10", aliases="[]")
            ).inserted_primary_key[0]

        self.client = TestClient(api.create_app(self.config_path))
        self.auth = ("admin", "secret")

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def create_asset(
        self,
        name: str,
        *,
        pending: bool = False,
        active: bool = True,
        operating_system_id: int | None = None,
    ) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                api.assets.insert().values(
                    assetname=name,
                    approved=api.APPROVAL_PENDING if pending else api.APPROVAL_NOT_PENDING,
                    systempass_hash=generate_password_hash("agent-secret") if pending else None,
                    active=active,
                    operatingsystem_id=operating_system_id,
                )
            ).inserted_primary_key[0]

    def assign(self, asset_id: int, attribute_id: int, *values: str | None) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                api.assignments.insert(),
                [
                    {
                        "asset_id": asset_id,
                        "attribute_id": attribute_id,
                        "value": value,
                        "assigned": True,
                    }
                    for value in values
                ],
            )

    def current_values(self, asset_id: int, attribute_id: int) -> list[str | None]:
        with self.engine.connect() as conn:
            return list(
                conn.execute(
                    api.select(api.assignments.c.value)
                    .where(
                        api.assignments.c.asset_id == asset_id,
                        api.assignments.c.attribute_id == attribute_id,
                        api.assignments.c.assigned.is_(True),
                    )
                    .order_by(api.assignments.c.id)
                ).scalars()
            )

    def asset_state(self, asset_id: int):
        with self.engine.connect() as conn:
            return conn.execute(
                api.select(
                    api.assets.c.active,
                    api.assets.c.approved,
                    api.assets.c.operatingsystem_id,
                ).where(api.assets.c.id == asset_id)
            ).one()

    def test_approval_mapping_transfers_missing_data_and_deactivates_manual_source(self) -> None:
        source_id = self.create_asset("manual-source", operating_system_id=self.os_id)
        target_id = self.create_asset("pending-agent", pending=True)
        self.assign(source_id, self.location_id, "dc1")
        self.assign(source_id, self.owner_id, "source-owner")
        self.assign(target_id, self.owner_id, "target-owner")
        self.assign(source_id, self.tags_id, "linux", "production")
        self.assign(target_id, self.tags_id, "production", "managed")

        response = self.client.post(
            f"/v1/assets/{target_id}/approve",
            json={"mode": "map_existing", "source_asset_id": source_id},
            auth=self.auth,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.asset_state(source_id).active)
        target_state = self.asset_state(target_id)
        self.assertTrue(target_state.active)
        self.assertEqual(target_state.approved, api.APPROVAL_APPROVED)
        self.assertEqual(target_state.operatingsystem_id, self.os_id)
        self.assertEqual(self.current_values(target_id, self.location_id), ["dc1"])
        self.assertEqual(self.current_values(target_id, self.owner_id), ["target-owner"])
        self.assertEqual(self.current_values(target_id, self.tags_id), ["production", "managed", "linux"])
        self.assertEqual(self.current_values(source_id, self.tags_id), ["linux", "production"])
        with self.engine.connect() as conn:
            audit_rows = conn.execute(
                api.select(api.audit_log.c.action, api.audit_log.c.details)
                .where(api.audit_log.c.action.in_(["merge_source", "merge_target"]))
                .order_by(api.audit_log.c.id)
            ).all()
        self.assertEqual([row.action for row in audit_rows], ["merge_source", "merge_target"])
        self.assertTrue(all('"workflow": "approval_map"' in row.details for row in audit_rows))

    def test_bodyless_approval_remains_approve_as_new(self) -> None:
        target_id = self.create_asset("pending-agent", pending=True)

        response = self.client.post(f"/v1/assets/{target_id}/approve", auth=self.auth)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.asset_state(target_id).approved, api.APPROVAL_APPROVED)

    def test_manual_merge_keeps_target_identity_and_existing_singleton(self) -> None:
        source_id = self.create_asset("manual-source")
        target_id = self.create_asset("managed-target", pending=True)
        with self.engine.begin() as conn:
            conn.execute(
                api.assets.update()
                .where(api.assets.c.id == target_id)
                .values(approved=api.APPROVAL_APPROVED)
            )
        self.assign(source_id, self.location_id, "source-location")
        self.assign(target_id, self.location_id, "target-location")
        self.assign(source_id, self.tags_id, "one", None)
        self.assign(target_id, self.tags_id, None)

        response = self.client.post(
            f"/v1/assets/{source_id}/merge",
            json={"target_asset_id": target_id},
            auth=self.auth,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], target_id)
        self.assertFalse(self.asset_state(source_id).active)
        self.assertEqual(self.current_values(target_id, self.location_id), ["target-location"])
        self.assertEqual(self.current_values(target_id, self.tags_id), [None, "one"])

    def test_candidates_scope_manual_assets_for_approval(self) -> None:
        manual_id = self.create_asset("manual")
        self.create_asset("agent", pending=True)
        self.create_asset("inactive-manual", active=False)

        approval = self.client.get(
            "/v1/assets/merge-candidates",
            params={"mode": "approval"},
            auth=self.auth,
        )
        merge = self.client.get(
            "/v1/assets/merge-candidates",
            params={"mode": "merge", "exclude_asset_id": manual_id},
            auth=self.auth,
        )

        self.assertEqual([item["assetname"] for item in approval.json()], ["manual"])
        self.assertEqual([item["assetname"] for item in merge.json()], ["agent"])

    def test_readonly_user_can_list_candidates_but_cannot_merge(self) -> None:
        source_id = self.create_asset("manual")
        target_id = self.create_asset("target")
        readonly_auth = ("reader", "secret")

        candidates = self.client.get(
            "/v1/assets/merge-candidates",
            params={"mode": "merge", "exclude_asset_id": source_id},
            auth=readonly_auth,
        )
        response = self.client.post(
            f"/v1/assets/{source_id}/merge",
            json={"target_asset_id": target_id},
            auth=readonly_auth,
        )

        self.assertEqual(candidates.status_code, 200)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(self.asset_state(source_id).active)

    def test_bulk_review_supports_mapping_approval_and_leave_pending(self) -> None:
        source_id = self.create_asset("manual")
        mapped_id = self.create_asset("pending-mapped", pending=True)
        approved_id = self.create_asset("pending-new", pending=True)
        left_id = self.create_asset("pending-left", pending=True)
        self.assign(source_id, self.location_id, "dc1")

        response = self.client.post(
            "/v1/assets/approve-all",
            json={
                "items": [
                    {"pending_asset_id": mapped_id, "action": "map_existing", "source_asset_id": source_id},
                    {"pending_asset_id": approved_id, "action": "approve_new"},
                    {"pending_asset_id": left_id, "action": "leave_pending"},
                ]
            },
            auth=self.auth,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.asset_state(mapped_id).approved, api.APPROVAL_APPROVED)
        self.assertEqual(self.asset_state(approved_id).approved, api.APPROVAL_APPROVED)
        self.assertEqual(self.asset_state(left_id).approved, api.APPROVAL_PENDING)
        self.assertFalse(self.asset_state(source_id).active)

    def test_bodyless_bulk_approval_approves_every_pending_asset_as_new(self) -> None:
        first_id = self.create_asset("pending-first", pending=True)
        second_id = self.create_asset("pending-second", pending=True)

        response = self.client.post("/v1/assets/approve-all", auth=self.auth)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.asset_state(first_id).approved, api.APPROVAL_APPROVED)
        self.assertEqual(self.asset_state(second_id).approved, api.APPROVAL_APPROVED)

    def test_bulk_review_rejects_duplicate_manual_source(self) -> None:
        source_id = self.create_asset("manual")
        first_id = self.create_asset("pending-first", pending=True)
        second_id = self.create_asset("pending-second", pending=True)

        response = self.client.post(
            "/v1/assets/approve-all",
            json={
                "items": [
                    {"pending_asset_id": first_id, "action": "map_existing", "source_asset_id": source_id},
                    {"pending_asset_id": second_id, "action": "map_existing", "source_asset_id": source_id},
                ]
            },
            auth=self.auth,
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.asset_state(source_id).active)
        self.assertEqual(self.asset_state(first_id).approved, api.APPROVAL_PENDING)
        self.assertEqual(self.asset_state(second_id).approved, api.APPROVAL_PENDING)

    def test_bulk_failure_rolls_back_earlier_approval(self) -> None:
        first_id = self.create_asset("pending-first", pending=True)
        second_id = self.create_asset("pending-second", pending=True)
        invalid_source_id = self.create_asset("agent-source", pending=True)

        response = self.client.post(
            "/v1/assets/approve-all",
            json={
                "items": [
                    {"pending_asset_id": first_id, "action": "approve_new"},
                    {
                        "pending_asset_id": second_id,
                        "action": "map_existing",
                        "source_asset_id": invalid_source_id,
                    },
                    {"pending_asset_id": invalid_source_id, "action": "leave_pending"},
                ]
            },
            auth=self.auth,
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.asset_state(first_id).approved, api.APPROVAL_PENDING)
        self.assertEqual(self.asset_state(second_id).approved, api.APPROVAL_PENDING)


if __name__ == "__main__":
    unittest.main()