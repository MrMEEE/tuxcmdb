"""Poll Git and run isolated package build Jobs; never build packages at image creation."""

from __future__ import annotations

from dataclasses import dataclass
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger("agents-updater")
TARGETS = ("el8", "el9", "el10", "debian")
SECURITY = {
    "allowPrivilegeEscalation": False,
    "capabilities": {"drop": ["ALL"]},
    "runAsNonRoot": True,
    "seccompProfile": {"type": "RuntimeDefault"},
}


def retention_value(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("Versions to keep must be a positive whole number")
    return value


def prune_releases(storage: Path, keep: int) -> None:
    """Keep distinct versions, with the current publication always protected."""
    root = storage / "releases"
    if not root.is_dir():
        return
    current = (storage / "current").resolve()
    releases = []
    for path in root.iterdir():
        if path.is_symlink() or not path.is_dir():
            continue
        marker = path / ".agent-version"
        if marker.is_file():
            version = marker.read_text().strip()
            if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version):
                releases.append((path, version))
    releases.sort(key=lambda item: (item[0] == current, item[0].stat().st_mtime), reverse=True)
    versions: set[str] = set()
    for path, version in releases:
        if version not in versions and len(versions) < keep:
            versions.add(version)
        else:
            LOG.info("Removing retired agent release %s", path.name)
            shutil.rmtree(path)


@dataclass(frozen=True)
class Config:
    namespace: str
    repository: str
    ref: str
    interval: int
    token: str
    storage: Path = Path("/var/lib/tuxcmdb-agents")

    @classmethod
    def from_env(cls) -> Config:
        interval = int(os.environ.get("AGENT_CHECK_INTERVAL", "3600"))
        if interval < 60:
            raise ValueError("AGENT_CHECK_INTERVAL must be at least 60 seconds")
        token = os.environ["AGENT_UPDATER_TOKEN"]
        if not token:
            raise ValueError("AGENT_UPDATER_TOKEN must not be empty")
        return cls(
            os.environ["POD_NAMESPACE"],
            os.environ["SOURCE_REPOSITORY_URL"],
            os.environ["SOURCE_REPOSITORY_REF"],
            interval,
            token,
        )


class Kubernetes:
    def __init__(self) -> None:
        self.credentials = Path("/var/run/secrets/kubernetes.io/serviceaccount")
        self.context = ssl.create_default_context(cafile=str(self.credentials / "ca.crt"))

    def request(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        token = (self.credentials / "token").read_text().strip()
        request = Request(
            "https://kubernetes.default.svc" + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urlopen(request, context=self.context, timeout=20) as response:
            return json.load(response)


def remote_commit(config: Config) -> str:
    if re.fullmatch(r"[0-9a-f]{40,64}", config.ref):
        return config.ref
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--", config.repository,
         config.ref, f"{config.ref}^{{}}", f"refs/heads/{config.ref}", f"refs/tags/{config.ref}",
         f"refs/tags/{config.ref}^{{}}"],
        text=True, capture_output=True, check=True, timeout=60,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    refs = dict(line.split()[::-1] for line in result.stdout.splitlines())
    # Prefer the explicitly named ref; dereference annotated tags to their commit.
    for ref in (f"{config.ref}^{{}}", config.ref, f"refs/heads/{config.ref}",
                f"refs/tags/{config.ref}^{{}}", f"refs/tags/{config.ref}"):
        if ref in refs and re.fullmatch(r"[0-9a-f]{40,64}", refs[ref]):
            return refs[ref]
    raise ValueError(f"No unambiguous Git commit found for ref {config.ref!r}")


def build_job(config: Config, commit: str, images: dict[str, str]) -> dict[str, Any]:
    mount = {"name": "work", "mountPath": "/work"}
    resources = {"requests": {"cpu": "100m", "memory": "256Mi"},
                 "limits": {"cpu": "2", "memory": "2Gi"}}

    def container(name: str, image: str, command: list[str]) -> dict[str, Any]:
        return {"name": name, "image": image, "command": command,
                "volumeMounts": [mount], "securityContext": SECURITY,
                "resources": resources, "env": [{"name": "HOME", "value": "/tmp"}]}

    checkout = container("checkout", images["updater"],
                         ["/bin/bash", "/usr/local/bin/agent-build-job", "checkout"])
    checkout["env"] += [
        {"name": "SOURCE_REPOSITORY_URL", "value": config.repository},
        {"name": "AGENT_COMMIT", "value": commit},
    ]
    builders = [
        container(target, images[target], [
            "/bin/bash", "-ec",
            f'umask 002; cd /work/src; bash tools/build-agent-package.sh {target} "$(cat /work/agent-version)"',
        ])
        for target in TARGETS
    ]
    publisher = container("publish", images["updater"],
                          ["/bin/bash", "/usr/local/bin/agent-build-job", "publish"])
    publisher["volumeMounts"].append(
        {"name": "downloads", "mountPath": "/var/lib/tuxcmdb-agents"}
    )
    return {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"generateName": "tuxcmdb-agent-packages-",
                     "labels": {"app": "tuxcmdb", "component": "agent-package-build"},
                     "annotations": {"tuxcmdb.io/agent-commit": commit}},
        "spec": {
            "backoffLimit": 0, "activeDeadlineSeconds": 3600, "ttlSecondsAfterFinished": 86400,
            "template": {
                "metadata": {"labels": {"app": "tuxcmdb", "component": "agent-package-build"}},
                "spec": {
                    "restartPolicy": "Never", "automountServiceAccountToken": False,
                    "initContainers": [checkout, *builders], "containers": [publisher],
                    "volumes": [
                        {"name": "work", "emptyDir": {}},
                        {"name": "downloads", "persistentVolumeClaim": {"claimName": "tuxcmdb-agents"}},
                    ],
                },
            },
        },
    }


class Controller:
    def __init__(self, config: Config, kube: Kubernetes) -> None:
        self.config = config
        self.kube = kube
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.manual = False
        self.status: dict[str, Any] = {"state": "starting", "job": "", "error": ""}
        self.next_check = 0.0
        self.settings_path = config.storage / "settings.json"
        self.keep = 3
        if self.settings_path.is_file():
            self.keep = retention_value(json.loads(self.settings_path.read_text())["versions_to_keep"])
        self.jobs_path = f"/apis/batch/v1/namespaces/{config.namespace}/jobs"

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {**self.status, "versions_to_keep": self.keep,
                    "check_interval": self.config.interval}

    def save_settings(self, keep: int) -> None:
        keep = retention_value(keep)
        with self.lock:
            self.config.storage.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix=".settings-", dir=self.config.storage)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "w") as stream:
                    os.fchmod(stream.fileno(), 0o644)
                    stream.write(json.dumps({"versions_to_keep": keep}) + "\n")
                temporary.replace(self.settings_path)
            finally:
                temporary.unlink(missing_ok=True)
            self.keep = keep
        self.wake.set()

    def update(self, **values: Any) -> None:
        with self.lock:
            self.status.update(values)

    def trigger(self) -> None:
        with self.lock:
            self.manual = True
        self.wake.set()

    def check(self, force: bool) -> None:
        jobs = self.kube.request(
            self.jobs_path + "?labelSelector=component%3Dagent-package-build"
        )["items"]
        for job in jobs:
            conditions = job.get("status", {}).get("conditions", [])
            if not any(item["type"] in ("Complete", "Failed") and item["status"] == "True"
                       for item in conditions):
                self.update(state="building", job=job["metadata"]["name"], error="")
                return
        prune_releases(self.config.storage, self.keep)
        previous_job = self.snapshot()["job"]
        for job in jobs:
            if job["metadata"]["name"] == previous_job:
                for condition in job.get("status", {}).get("conditions", []):
                    if condition["type"] == "Failed" and condition["status"] == "True":
                        self.update(state="error", error=f"Build Job {previous_job} failed; inspect its pod logs.")
                    elif condition["type"] == "Complete" and condition["status"] == "True":
                        self.update(state="up-to-date", error="")
        if not force and time.monotonic() < self.next_check:
            return
        commit = remote_commit(self.config)
        published = self.config.storage / "current" / ".agent-commit"
        if not force and published.is_file() and published.read_text().strip() == commit:
            self.next_check = time.monotonic() + self.config.interval
            self.update(state="up-to-date", commit=commit, error="")
            return
        images = {}
        for target in ("updater", *TARGETS):
            name = "tuxcmdb-agents" if target == "updater" else f"tuxcmdb-agent-builder-{target}"
            image = self.kube.request(
                f"/apis/image.openshift.io/v1/namespaces/{self.config.namespace}/imagestreamtags/{name}:latest"
            )
            images[target] = image["image"]["dockerImageReference"]
        job = self.kube.request(self.jobs_path, build_job(self.config, commit, images))
        self.next_check = time.monotonic() + self.config.interval
        LOG.info("Started %s for %s", job["metadata"]["name"], commit)
        self.update(state="building", job=job["metadata"]["name"], commit=commit, error="")

    def run(self) -> None:
        while True:
            self.wake.clear()
            with self.lock:
                force, self.manual = self.manual, False
            try:
                self.check(force)
            except (HTTPError, URLError, OSError, ValueError, KeyError,
                    subprocess.SubprocessError) as exc:
                LOG.exception("Agent update check failed")
                self.update(state="error", error=str(exc))
            self.wake.wait(30)


def handler(controller: Controller) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def respond(self, code: int, body: dict[str, Any]) -> None:
            content = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def authorized(self) -> bool:
            expected = "Bearer " + controller.config.token
            if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected.encode()):
                self.respond(403, {"error": "Forbidden"})
                return False
            return True

        def do_GET(self) -> None:
            if self.path == "/health":
                self.respond(200, {"ready": True})
            elif self.path == "/status" and self.authorized():
                self.respond(200, controller.snapshot())
            elif self.path != "/status":
                self.respond(404, {"error": "Not found"})

        def do_POST(self) -> None:
            if self.path not in ("/build", "/settings"):
                self.respond(404, {"error": "Not found"})
            elif self.authorized():
                if self.path == "/build":
                    controller.trigger()
                    self.respond(202, {"state": "queued"})
                else:
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        if not 0 < length <= 1024:
                            raise ValueError("Invalid settings request size")
                        data = json.loads(self.rfile.read(length))
                        if not isinstance(data, dict):
                            raise ValueError("Expected a settings object")
                        keep = retention_value(data.get("versions_to_keep"))
                    except (ValueError, UnicodeError) as exc:
                        self.respond(400, {"error": str(exc)})
                        return
                    try:
                        controller.save_settings(keep)
                    except OSError:
                        LOG.exception("Could not save agent settings")
                        self.respond(500, {"error": "Could not persist agent settings"})
                        return
                    self.respond(200, controller.snapshot())

    return Handler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = Config.from_env()
    controller = Controller(config, Kubernetes())
    threading.Thread(target=controller.run, daemon=True).start()
    LOG.info("Checking %s (%s) every %s seconds", config.repository, config.ref, config.interval)
    HTTPServer(("0.0.0.0", 8080), handler(controller)).serve_forever()


if __name__ == "__main__":
    main()
