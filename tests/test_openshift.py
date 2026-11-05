from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from werkzeug.security import check_password_hash
import yaml

from tuxcmdb.db import create_db_engine

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "openshift_runtime", ROOT / "deploy" / "openshift" / "runtime.py"
)
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)

DATABASE_ENV = {
    "DB_BACKEND": "mariadb",
    "DB_HOST": "tuxcmdb-database",
    "DB_PORT": "3306",
    "DB_NAME": "tuxcmdb",
    "DB_USER": "tuxcmdb",
    "DB_PASSWORD": "password",
}


class OpenShiftRuntimeTests(unittest.TestCase):
    def test_connection_pool_checks_stale_connections_unless_overridden(self) -> None:
        for options, expected in [({}, True), ({"pool_pre_ping": False}, False)]:
            engine = create_db_engine("sqlite:///:memory:", **options)
            self.assertEqual(engine.pool._pre_ping, expected)
            engine.dispose()

    def test_database_drivers_and_password_encoding(self) -> None:
        for backend, driver, port in [
            ("mariadb", "mysql+pymysql", "3306"),
            ("postgresql", "postgresql+psycopg", "5432"),
        ]:
            with self.subTest(backend=backend), patch.dict(
                os.environ,
                {**DATABASE_ENV, "DB_BACKEND": backend, "DB_PORT": port, "DB_PASSWORD": "p@ss:/%+#"},
                clear=True,
            ):
                url = make_url(runtime.database_url())
                self.assertEqual(url.drivername, driver)
                self.assertEqual(url.password, "p@ss:/%+#")
                self.assertEqual(url.port, int(port))

    def test_missing_database_credentials_fail(self) -> None:
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(KeyError):
            runtime.database_url()

    def test_runtime_config_points_webui_to_api_service(self) -> None:
        for component, host in [("api", "0.0.0.0"), ("webui", "tuxcmdb-api")]:
            with self.subTest(component=component), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                api_config = root / "tuxcmdb-api" / "conf" / "api.yaml"
                with patch.object(runtime, "ROOT", root), patch.object(
                    runtime, "API_CONFIG", api_config
                ), patch.dict(os.environ, {**DATABASE_ENV, "TUXCMDB_COMPONENT": component}, clear=True):
                    runtime.configure()
                api = yaml.safe_load(api_config.read_text())["api"]
                database_path = root / "conf" / "database.yaml"
                database = yaml.safe_load(database_path.read_text())["database"]
                self.assertEqual(api["database_url"], database["url"])
                self.assertEqual(api["host"], host)
                self.assertEqual(api["port"], 8080)
                self.assertEqual(api_config.stat().st_mode & 0o777, 0o600)
                self.assertEqual(database_path.stat().st_mode & 0o777, 0o600)

    def test_unknown_component_fails(self) -> None:
        with patch.dict(os.environ, {"TUXCMDB_COMPONENT": "invalid"}, clear=True):
            with self.assertRaisesRegex(ValueError, "Unsupported TUXCMDB_COMPONENT"):
                runtime.configure()

    def test_migrations_and_admin_bootstrap_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            url = f"sqlite+pysqlite:///{directory}/test.db"
            api_config = Path(directory) / "api.yaml"
            api_config.write_text(yaml.safe_dump({"api": {"database_url": url}}))
            with patch.object(runtime, "database_url", return_value=url), patch.object(
                runtime, "API_CONFIG", api_config
            ), patch.dict(
                os.environ, {"TUXCMDB_ADMIN_USER": "admin", "TUXCMDB_ADMIN_PASSWORD": "first-password"}
            ):
                runtime.migrate()
                engine = create_engine(url)
                with engine.begin() as conn:
                    conn.execute(text("UPDATE apiusers SET is_active = false WHERE username = 'admin'"))
                with patch.dict(os.environ, {"TUXCMDB_ADMIN_PASSWORD": "changed-password"}):
                    runtime.migrate()
                with engine.connect() as conn:
                    users = conn.execute(
                        text("SELECT username, password_hash, is_active FROM apiusers")
                    ).mappings().all()
                engine.dispose()
            self.assertEqual(len(users), 1)
            self.assertTrue(check_password_hash(users[0]["password_hash"], "first-password"))
            self.assertFalse(users[0]["is_active"])


@unittest.skipUnless(shutil.which("oc"), "OpenShift CLI not installed")
class OpenShiftTemplateTests(unittest.TestCase):
    def render(self, name: str, *parameters: str) -> list[dict]:
        command = ["oc", "process", "--local", "-f", str(ROOT / "deploy/openshift" / name), "-o", "json"]
        for parameter in parameters:
            command.extend(["-p", parameter])
        return json.loads(subprocess.check_output(command, text=True))["items"]

    def test_database_options_have_numeric_matching_ports(self) -> None:
        for backend, port in [("mariadb", 3306), ("postgresql", 5432)]:
            with self.subTest(backend=backend):
                objects = self.render("database.yaml", f"DB_BACKEND={backend}", f"DB_PORT={port}")
                deployment = next(obj for obj in objects if obj["kind"] == "Deployment")
                container = deployment["spec"]["template"]["spec"]["containers"][0]
                service = next(obj for obj in objects if obj["kind"] == "Service")
                self.assertEqual(container["ports"][0]["containerPort"], port)
                self.assertEqual(service["spec"]["ports"][0]["port"], port)
                self.assertEqual(deployment["spec"]["strategy"]["type"], "Recreate")
                builder = next(
                    obj for obj in objects
                    if obj["kind"] == "BuildConfig" and obj["metadata"]["name"].endswith("-builder")
                )
                self.assertEqual(
                    builder["spec"]["strategy"]["dockerStrategy"]["dockerfilePath"],
                    f"{backend}/Dockerfile",
                )
                self.assertEqual(builder["spec"]["triggers"], [{"type": "ConfigChange"}])
                runtime_build = next(
                    obj for obj in objects
                    if obj["kind"] == "BuildConfig" and obj["metadata"]["name"] == "tuxcmdb-database"
                )
                self.assertEqual(runtime_build["spec"]["triggers"], [{"type": "ImageChange"}])
                self.assertEqual(
                    runtime_build["spec"]["strategy"]["sourceStrategy"]["from"]["name"],
                    builder["spec"]["output"]["to"]["name"],
                )

    def test_server_uses_s2i_and_tls_routes(self) -> None:
        objects = self.render(
            "server.yaml", "WEBUI_HOST=cmdb.apps.example.com", "API_HOST=api.apps.example.com"
        )
        builds = {obj["metadata"]["name"]: obj for obj in objects if obj["kind"] == "BuildConfig"}
        self.assertEqual(set(builds), {"tuxcmdb-api", "tuxcmdb-webui"})
        streams = {obj["metadata"]["name"] for obj in objects if obj["kind"] == "ImageStream"}
        self.assertEqual(streams, set(builds))
        for name, build in builds.items():
            self.assertEqual(build["spec"]["strategy"]["type"], "Source")
            strategy = build["spec"]["strategy"]["sourceStrategy"]
            self.assertIn("/ubi10/", strategy["from"]["name"])
            env = {item["name"]: item["value"] for item in strategy["env"]}
            self.assertEqual(env["TUXCMDB_COMPONENT"], name.removeprefix("tuxcmdb-"))
            self.assertNotIn("TUXCMDB_INCLUDE_AGENTS", env)
            self.assertNotIn("images", build["spec"]["source"])
            self.assertEqual(build["spec"]["output"]["to"]["name"], f"{name}:latest")
        deployments = [obj for obj in objects if obj["kind"] == "Deployment"]
        self.assertEqual(len(deployments), 2)
        for deployment in deployments:
            self.assertEqual(deployment["spec"]["replicas"], 1)
            self.assertEqual(deployment["spec"]["strategy"]["type"], "Recreate")
            container = deployment["spec"]["template"]["spec"]["containers"][0]
            self.assertTrue(container["securityContext"]["runAsNonRoot"])
            name = deployment["metadata"]["name"]
            self.assertEqual(len(deployment["spec"]["template"]["spec"]["containers"]), 1)
            self.assertEqual(container["image"], f"{name}:latest")
            triggers = json.loads(deployment["metadata"]["annotations"]["image.openshift.io/triggers"])
            self.assertEqual(triggers[0]["from"]["name"], builds[name]["spec"]["output"]["to"]["name"])
        routes = [obj for obj in objects if obj["kind"] == "Route"]
        self.assertEqual(len(routes), 2)
        for route in routes:
            self.assertEqual(route["spec"]["tls"]["termination"], "edge")
            self.assertEqual(route["spec"]["tls"]["insecureEdgeTerminationPolicy"], "Redirect")

    def test_all_buildconfigs_keep_one_successful_and_one_failed_build(self) -> None:
        templates = [
            ("server.yaml", ["WEBUI_HOST=cmdb.apps.example.com", "API_HOST=api.apps.example.com"]),
            ("agents.yaml", ["AGENTS_STORAGE_CLASS=shared-rwx"]),
            ("database.yaml", ["DB_BACKEND=mariadb"]),
            ("database.yaml", ["DB_BACKEND=postgresql"]),
        ]
        for template, parameters in templates:
            for obj in self.render(template, *parameters):
                if obj["kind"] == "BuildConfig":
                    with self.subTest(template=template, name=obj["metadata"]["name"]):
                        self.assertEqual(obj["spec"]["successfulBuildsHistoryLimit"], 1)
                        self.assertEqual(obj["spec"]["failedBuildsHistoryLimit"], 1)

    def test_optional_agents_use_shared_storage_without_webui_build_dependency(self) -> None:
        objects = self.render(
            "agents.yaml", "SOURCE_REPOSITORY_REF=agent-test", "AGENTS_STORAGE_CLASS=shared-rwx"
        )
        builds = {obj["metadata"]["name"]: obj for obj in objects if obj["kind"] == "BuildConfig"}
        self.assertEqual(set(builds), {"tuxcmdb-agents", *(
            f"tuxcmdb-agent-builder-{target}" for target in ("el8", "el9", "el10", "debian")
        )})
        self.assertFalse(any(obj["kind"] == "Job" for obj in objects))
        agents = builds["tuxcmdb-agents"]
        self.assertEqual(agents["spec"]["strategy"]["type"], "Docker")
        self.assertEqual(
            agents["spec"]["strategy"]["dockerStrategy"]["dockerfilePath"],
            "deploy/openshift/agents/Dockerfile",
        )
        self.assertNotIn("buildArgs", agents["spec"]["strategy"]["dockerStrategy"])
        self.assertEqual(agents["spec"]["triggers"], [{"type": "ConfigChange"}])
        image_tag = agents["spec"]["output"]["to"]
        updater = next(obj for obj in objects if obj["kind"] == "Deployment")
        triggers = json.loads(updater["metadata"]["annotations"]["image.openshift.io/triggers"])
        self.assertEqual(triggers[0]["from"], image_tag)
        self.assertEqual(updater["spec"]["strategy"]["type"], "Recreate")
        self.assertEqual(updater["spec"]["replicas"], 1)
        self.assertEqual(
            updater["spec"]["template"]["spec"]["containers"][0]["command"],
            ["/usr/bin/python3", "-u", "/usr/local/lib/agent-controller.py"],
        )
        self.assertEqual(updater["spec"]["template"]["spec"]["serviceAccountName"], "tuxcmdb-agents-updater")
        role = next(obj for obj in objects if obj["kind"] == "Role")
        self.assertEqual(role["rules"][0]["verbs"], ["get", "list", "create"])
        self.assertEqual(role["rules"][0]["resources"], ["jobs"])
        claim = next(obj for obj in objects if obj["kind"] == "PersistentVolumeClaim")
        self.assertEqual(claim["spec"]["accessModes"], ["ReadWriteMany"])
        self.assertEqual(claim["spec"]["storageClassName"], "shared-rwx")
        for build in builds.values():
            self.assertEqual(build["spec"]["source"]["git"]["ref"], "agent-test")
            dockerfile = ROOT / build["spec"]["strategy"]["dockerStrategy"]["dockerfilePath"]
            content = dockerfile.read_text()
            self.assertNotIn("build-agent-package.sh", content)
            self.assertNotIn("COPY agent/", content)
        server = self.render(
            "server.yaml", "WEBUI_HOST=cmdb.apps.example.com", "API_HOST=api.apps.example.com"
        )
        deployment = next(obj for obj in server
                          if obj["kind"] == "Deployment" and obj["metadata"]["name"] == "tuxcmdb-webui")
        env = {
            entry["name"]: entry["value"]
            for entry in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(env["TUXCMDB_AGENTS_DIR"], "/opt/app-root/src/agent-artifacts/current")
        self.assertEqual(env["TUXCMDB_AGENT_INSTALL_SERVER_URL"], "https://api.apps.example.com")
        patch = yaml.safe_load((ROOT / "deploy/openshift/agents-webui-patch.yaml").read_text())
        spec = patch["spec"]["template"]["spec"]
        mount = spec["containers"][0]["volumeMounts"][0]
        self.assertTrue(mount["readOnly"])
        self.assertEqual(mount["mountPath"], "/opt/app-root/src/agent-artifacts")
        self.assertEqual(spec["volumes"][0]["persistentVolumeClaim"]["claimName"], "tuxcmdb-agents")
        env = {entry["name"]: entry for entry in spec["containers"][0]["env"]}
        self.assertEqual(env["TUXCMDB_AGENT_UPDATER_TOKEN"]["valueFrom"]["secretKeyRef"]["name"],
                         "tuxcmdb-agents-updater")


if __name__ == "__main__":
    unittest.main()
