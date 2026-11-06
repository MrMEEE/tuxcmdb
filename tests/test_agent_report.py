"""A rejected reported value must not roll back unrelated inventory updates."""

import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from werkzeug.security import generate_password_hash
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tuxcmdb-api"), str(ROOT)]
api = importlib.import_module("app")


class AgentReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        url = f"sqlite+pysqlite:///{self.directory.name}/report.db"
        self.engine = api.create_db_engine(url)
        self.addCleanup(self.engine.dispose)
        api.metadata.create_all(self.engine)
        config = Path(self.directory.name) / "api.yaml"
        config.write_text(yaml.safe_dump({"api": {"database_url": url}}))
        self.client = TestClient(api.create_app(config))
        self.addCleanup(self.client.close)
        with self.engine.begin() as conn:
            self.asset_id = conn.execute(api.assets.insert().values(
                assetname="report-host", active=True, approved=2,
                systempass_hash=generate_password_hash("agent-secret"),
            )).inserted_primary_key[0]
            self.ids = {}
            for name, datatype in (("report-first", "string"), ("report-boolean", "boolean"),
                                   ("report-last", "string")):
                self.ids[name] = conn.execute(api.attributes.insert().values(
                    name=name, data_type=datatype, allow_multiple=False,
                )).inserted_primary_key[0]
            conn.execute(api.assignments.insert().values(
                asset_id=self.asset_id, attribute_id=self.ids["report-boolean"],
                value="true", assigned=True,
            ))

    def report(self, values, **kwargs):
        return self.client.post("/v1/agent/report", json={
            "asset_id": self.asset_id, "systempass": "agent-secret", "values": values, **kwargs,
        })

    def active_values(self):
        with self.engine.connect() as conn:
            rows = conn.execute(api.select(
                api.assignments.c.attribute_id, api.assignments.c.value,
            ).where(api.assignments.c.asset_id == self.asset_id,
                    api.assignments.c.assigned.is_(True))).all()
        return {row.attribute_id: row.value for row in rows}

    def test_bad_boolean_preserves_old_value_and_updates_surrounding_attributes(self):
        with self.assertLogs("app", level="WARNING") as logs:
            response = self.report([
                {"attribute_name": "report-first", "value": "before"},
                {"attribute_name": "report-boolean", "value": "not-a-boolean-private-value"},
                {"attribute_name": "report-last", "value": "after"},
            ])
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual((data["status"], data["updated_count"], data["rejected_count"]), ("partial", 2, 1))
        self.assertEqual(data["errors"][0]["index"], 1)
        self.assertEqual(data["errors"][0]["attribute_name"], "report-boolean")
        self.assertNotIn("not-a-boolean-private-value", response.text + str(logs.output))
        self.assertEqual(self.active_values(), {
            self.ids["report-first"]: "before", self.ids["report-boolean"]: "true",
            self.ids["report-last"]: "after",
        })
        with self.engine.connect() as conn:
            self.assertIsNotNone(conn.execute(api.select(api.assets.c.last_checkin_at).where(
                api.assets.c.id == self.asset_id)).scalar_one())
            audit = conn.execute(api.select(api.audit_log.c.details).where(
                api.audit_log.c.action == "agent_report")).scalar_one()
        self.assertIn('"rejected_count": 1', audit)

    def test_all_invalid_and_unknown_are_explicit_rejections(self):
        with self.assertLogs("app", level="WARNING"):
            response = self.report([
                {"attribute_name": "report-boolean", "value": "invalid"},
                {"attribute_name": "missing", "value": "anything"},
            ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rejected_count"], 2)
        self.assertEqual(response.json()["updated_count"], 0)
        self.assertEqual(self.active_values(), {self.ids["report-boolean"]: "true"})

    def test_unchanged_and_valid_values_remain_successful(self):
        response = self.report([{"attribute_name": "report-boolean", "value": "true"}])
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["unchanged_count"], 1)
        self.assertEqual(response.json()["errors"], [])

    def validation_status(self):
        with self.engine.connect() as conn:
            row = conn.execute(api.select(api.assets).where(api.assets.c.id == self.asset_id)).one()
            return api.build_asset_out([row], conn)[0]

    def test_badge_persists_with_old_value_and_clears_on_valid_unchanged_report(self):
        with self.assertLogs("app", level="WARNING"):
            self.report([{"attribute_name": "report-boolean", "value": "invalid"}])
        asset = self.validation_status()
        self.assertEqual(asset.invalid_attributes[0].attribute_name, "report-boolean")
        self.assertEqual(asset.attributes[0].value, "true")
        self.report([{"attribute_name": "report-first", "value": "unrelated"}])
        self.assertEqual(len(self.validation_status().invalid_attributes), 1)
        result = self.report([{"attribute_name": "report-boolean", "value": "true"}])
        self.assertEqual(result.json()["unchanged_count"], 1)
        self.assertEqual(self.validation_status().invalid_attributes, [])

    def test_never_valid_attribute_has_status_but_no_assignment(self):
        with self.engine.begin() as conn:
            conn.execute(api.assignments.delete().where(api.assignments.c.asset_id == self.asset_id))
        for _ in range(2):
            with self.assertLogs("app", level="WARNING"):
                self.report([{"attribute_name": "report-boolean", "value": "invalid"}])
        asset = self.validation_status()
        self.assertEqual(asset.attributes, [])
        self.assertEqual(len(asset.invalid_attributes), 1)
        self.report([{"attribute_name": "report-boolean", "value": "false"}])
        self.assertEqual(self.validation_status().invalid_attributes, [])
        self.assertEqual(self.active_values()[self.ids["report-boolean"]], "false")

    def test_invalid_credentials_do_not_process_values(self):
        response = self.report([{"attribute_name": "report-first", "value": "bad"}],
                               systempass="incorrect")
        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(self.active_values(), {self.ids["report-boolean"]: "true"})

    def test_asset_approval_and_decommissioning_still_gate_entire_report(self):
        for updates, expected in (({"approved": 1}, 403), ({"approved": 2, "active": False}, 409)):
            with self.subTest(updates=updates):
                with self.engine.begin() as conn:
                    conn.execute(api.assets.update().where(api.assets.c.id == self.asset_id).values(**updates))
                response = self.report([{"attribute_name": "report-first", "value": "blocked"}])
                self.assertEqual(response.status_code, expected)
                self.assertEqual(self.active_values(), {self.ids["report-boolean"]: "true"})

    def test_invalid_value_does_not_prevent_later_valid_value_for_same_attribute(self):
        with self.assertLogs("app", level="WARNING"):
            response = self.report([
                {"attribute_name": "report-boolean", "value": "invalid"},
                {"attribute_name": "report-boolean", "value": "false"},
            ])
        self.assertEqual(response.json()["updated_count"], 1)
        self.assertEqual(response.json()["rejected_count"], 1)
        self.assertEqual(self.active_values(), {self.ids["report-boolean"]: "false"})

    def test_unexpected_validation_failure_still_rolls_back(self):
        original = api.validate_attribute_value

        def validate(conn, datatype, value):
            if value == "server-error":
                raise api.HTTPException(status_code=500, detail="Invalid validator configuration")
            return original(conn, datatype, value)

        with patch.object(api, "validate_attribute_value", side_effect=validate):
            response = self.report([
                {"attribute_name": "report-first", "value": "before"},
                {"attribute_name": "report-last", "value": "server-error"},
            ])
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.active_values(), {self.ids["report-boolean"]: "true"})
