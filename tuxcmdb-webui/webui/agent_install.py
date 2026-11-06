"""Password SSH deployment with durable, credential-free progress records."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import shlex
import socket
import threading
import time
import uuid
from typing import Any

from django.conf import settings
import paramiko

LOG = logging.getLogger(__name__)
STEPS = (
    ("connect", "Connect through SSH"),
    ("os", "Detect operating system"),
    ("transfer", "Transfer agent package"),
    ("install", "Install package"),
    ("account", "Verify unprivileged service account"),
    ("register", "Register agent"),
    ("timer", "Activate systemd timer"),
    ("cleanup", "Remove temporary files"),
)


class InstallError(Exception):
    pass


def select_target(os_release: str) -> str:
    values = {}
    for line in os_release.splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            parts = shlex.split(value)
            values[key] = " ".join(parts)
    family = set((values.get("ID", "") + " " + values.get("ID_LIKE", "")).split())
    if family & {"debian", "ubuntu"}:
        return "debian"
    if family & {"rhel", "centos", "rocky", "almalinux", "ol"}:
        major = values.get("VERSION_ID", "").split(".")[0]
        if major in ("8", "9", "10"):
            return "el" + major
    raise InstallError("Unsupported OS: requires EL8/9/10 or Debian/Ubuntu with systemd.")


def _write(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(data, stream)
    temporary.replace(path)


def _lock_file(directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return (directory / "installer.lock").open("a")


def start(owner: str, options: dict[str, Any], packages: dict[str, Path]) -> str:
    directory = settings.AGENT_INSTALL_DATA_DIR
    lock = _lock_file(directory)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise InstallError("An installation is already running. Wait for it to finish.") from exc
    except OSError:
        lock.close()
        raise
    job_id = uuid.uuid4().hex
    path = directory / f"{job_id}.json"
    data = {
        "id": job_id, "owner": owner, "state": "running",
        "hosts": [{"host": host, "state": "queued", "steps": [
            {"id": step, "label": label, "state": "pending", "message": ""}
            for step, label in STEPS
        ]} for host in options["hosts"]],
    }
    try:
        _write(path, data)
        worker = threading.Thread(target=_run, args=(path, data, options, packages, lock), daemon=True)
        worker.start()
    except (OSError, RuntimeError):
        lock.close()
        raise
    return job_id


def status(job_id: str, owner: str) -> dict[str, Any] | None:
    if len(job_id) != 32 or any(char not in "0123456789abcdef" for char in job_id):
        return None
    directory = settings.AGENT_INSTALL_DATA_DIR
    path = directory / f"{job_id}.json"
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    if data["owner"] != owner:
        return None
    if data["state"] == "running":
        with _lock_file(directory) as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return data
            data["state"] = "interrupted"
            for host in data["hosts"]:
                if host["state"] in ("running", "queued"):
                    host["state"] = "interrupted"
                    for step in host["steps"]:
                        if step["state"] in ("running", "pending"):
                            step.update(state="interrupted", message="WebUI restarted; inspect the host before retrying.")
            _write(path, data)
    return data


def _command(client: paramiko.SSHClient, command: str, options: dict[str, Any],
             privileged: bool = True, timeout: int = 180) -> None:
    command = "/bin/bash -c " + shlex.quote(command)
    if privileged and options["use_sudo"]:
        command = "sudo -S -p '' -- " + command
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if privileged and options["use_sudo"]:
        stdin.write(options["password"] + "\n")
        stdin.flush()
    channel = stdout.channel
    channel.shutdown_write()
    deadline = time.monotonic() + timeout
    try:
        while True:
            # Drain both streams without retaining remote output, which can contain secrets.
            if channel.recv_ready():
                channel.recv(65536)
            if channel.recv_stderr_ready():
                channel.recv_stderr(65536)
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                code = channel.recv_exit_status()
                if code:
                    raise InstallError(f"Remote command failed (exit {code}). Check privileges, host prerequisites and server connectivity.")
                return
            if time.monotonic() >= deadline:
                raise InstallError("Remote command timed out; inspect the host before retrying.")
            time.sleep(0.05)
    finally:
        channel.close()
        stdin.close()
        stdout.close()
        stderr.close()


def _register_command(server_url: str, insecure: bool) -> str:
    code = """
import json, os, runpy, sys
path = "/etc/tuxcmdb-agent/config.json"
if os.path.exists(path):
    with open(path) as stream:
        old = json.load(stream)
    if old.get("server_url", "").rstrip("/") != sys.argv[1] or old.get("verify_ssl", True) != (sys.argv[2] == "false"):
        raise SystemExit("Existing agent configuration targets a different server or TLS policy; manual reconciliation required")
agent = runpy.run_path("/opt/tuxcmdb-agent/tuxcmdb_agent.py")
args = ["--server-url", sys.argv[1]]
if sys.argv[2] == "true":
    args.append("--insecure")
agent["ensure_config"](agent["build_arg_parser"]().parse_args(args))
"""
    return shlex.join([
        "runuser", "-u", "tuxcmdb-agent", "--", "/usr/bin/env",
        "PYTHONPATH=/opt/tuxcmdb-agent/lib", "/usr/bin/python3", "-c", code,
        server_url, "true" if insecure else "false",
    ])


def _install_host(host: dict[str, Any], options: dict[str, Any], packages: dict[str, Path],
                  update) -> None:
    client = paramiko.SSHClient()
    sftp = None
    remote_dir = ""
    remote_file = ""
    current = "connect"
    known_hosts = settings.AGENT_INSTALL_DATA_DIR / "known_hosts"
    try:
        if known_hosts.exists():
            client.load_host_keys(str(known_hosts))
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        update("connect", "running", "Connecting; new SSH keys are recorded, changed keys rejected.")
        client.connect(host["host"], username=options["username"], password=options["password"],
                       look_for_keys=False, allow_agent=False, timeout=15,
                       auth_timeout=15, banner_timeout=15)
        client.save_host_keys(str(known_hosts))
        os.chmod(known_hosts, 0o600)
        update("connect", "success", "SSH connection established.")
        current = "os"
        update(current, "running", "Reading /etc/os-release and checking systemd.")
        sftp = client.open_sftp()
        with sftp.open("/etc/os-release") as stream:
            target = select_target(stream.read(16384).decode())
        _command(client, "test -d /run/systemd/system && command -v systemctl >/dev/null",
                 options, privileged=False)
        if target not in packages:
            raise InstallError(f"No published {target} package is available. Build or install agent downloads first.")
        update(current, "success", f"Selected {target} package.")
        current = "transfer"
        update(current, "running", "Uploading the package over SSH.")
        remote_dir = "/tmp/tuxcmdb-install-" + uuid.uuid4().hex
        sftp.mkdir(remote_dir, mode=0o700)
        package = packages[target]
        remote_file = remote_dir + "/" + package.name
        digest = hashlib.sha256()
        with package.open("rb") as source, sftp.open(remote_file, "wb") as destination:
            for chunk in iter(lambda: source.read(65536), b""):
                digest.update(chunk)
                destination.write(chunk)
        checksum = shlex.quote(digest.hexdigest() + "  " + remote_file)
        _command(client, f"printf '%s\\n' {checksum} | sha256sum -c -", options, privileged=False)
        update(current, "success", "Package uploaded and checksum verified.")
        current = "install"
        update(current, "running", "Installing package and dependencies.")
        if target == "debian":
            # apt's sandbox user must be able to read the package without traversing the private upload directory.
            command = (
                f"chmod 755 {shlex.quote(remote_dir)}; chmod 644 {shlex.quote(remote_file)}; "
                "export DEBIAN_FRONTEND=noninteractive; apt-get update && "
                f"apt-get install -y -- {shlex.quote(remote_file)}"
            )
        else:
            command = f"dnf install -y -- {shlex.quote(remote_file)}"
        _command(client, "set -e; test \"$(id -u)\" = 0; " + command, options, timeout=600)
        update(current, "success", "Agent package installed.")
        current = "account"
        update(current, "running", "Checking tuxcmdb-agent and the service configuration.")
        _command(client, """
set -e
getent passwd tuxcmdb-agent >/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin tuxcmdb-agent
test "$(id -u tuxcmdb-agent)" != 0
install -d -o tuxcmdb-agent -g tuxcmdb-agent -m 750 /etc/tuxcmdb-agent
systemctl daemon-reload
test "$(systemctl show tuxcmdb-agent.service -p User --value)" = tuxcmdb-agent
""", options)
        update(current, "success", "Service runs as the non-privileged tuxcmdb-agent account.")
        current = "register"
        update(current, "running", "Registering as tuxcmdb-agent; existing matching registration is preserved.")
        _command(client, _register_command(options["server_url"], options["ignore_ssl"]), options)
        update(current, "success", "Agent registered. Asset approval may still be required.")
        current = "timer"
        update(current, "running", "Enabling and starting the systemd timer.")
        _command(client, "systemctl enable --now tuxcmdb-agent.timer && systemctl is-active --quiet tuxcmdb-agent.timer", options)
        update(current, "success", "Agent timer enabled and active.")
    except (InstallError, paramiko.SSHException, OSError, ValueError, EOFError) as exc:
        if isinstance(exc, paramiko.AuthenticationException):
            message = "SSH authentication failed."
        elif isinstance(exc, paramiko.BadHostKeyException):
            message = "SSH host key changed. Verify the host and update the installer's known_hosts file manually."
        elif isinstance(exc, InstallError):
            message = str(exc)
            if current == "register":
                message += " Check API reachability, certificate trust, and any existing agent server/TLS configuration."
        elif isinstance(exc, socket.gaierror):
            message = "Hostname could not be resolved from the WebUI server. Check DNS and the hostname."
        elif isinstance(exc, (socket.timeout, TimeoutError)):
            message = "SSH connection or operation timed out."
        else:
            message = f"SSH/file operation failed ({type(exc).__name__}). Check host reachability and permissions."
        LOG.warning("Agent installation failed for %s at %s (%s)", host["host"], current, type(exc).__name__)
        update(current, "failed", message)
    finally:
        update("cleanup", "running", "Removing temporary package upload.")
        try:
            if sftp is not None and remote_file:
                try:
                    sftp.remove(remote_file)
                except FileNotFoundError:
                    pass
            if sftp is not None and remote_dir:
                sftp.rmdir(remote_dir)
            update("cleanup", "success", "Temporary files removed.")
        except (OSError, paramiko.SSHException, EOFError):
            LOG.warning("Could not remove temporary agent upload on %s", host["host"])
            update("cleanup", "failed", "Could not remove the temporary upload; clean it up on the host.")
        finally:
            if sftp is not None:
                try:
                    sftp.close()
                except (OSError, paramiko.SSHException, EOFError):
                    LOG.warning("Could not close the SFTP session on %s", host["host"])
            client.close()


def _run(path: Path, data: dict[str, Any], options: dict[str, Any],
         packages: dict[str, Path], lock) -> None:
    try:
        for host in data["hosts"]:
            host["state"] = "running"

            def update(step_id, state, message):
                step = next(item for item in host["steps"] if item["id"] == step_id)
                step.update(state=state, message=message)
                _write(path, data)

            _install_host(host, options, packages, update)
            failed = any(step["state"] == "failed" for step in host["steps"])
            host["state"] = "failed" if failed else "success"
            for step in host["steps"]:
                if step["state"] == "pending":
                    step["state"] = "skipped"
            _write(path, data)
        data["state"] = "finished"
        _write(path, data)
    except Exception:
        # Preserve evidence of worker failures without logging credentials or remote output.
        LOG.error("Agent installation worker failed unexpectedly; job %s", data["id"])
        data["state"] = "interrupted"
        _write(path, data)
        raise
    finally:
        options.clear()
        lock.close()
