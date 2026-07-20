#!/usr/bin/env python3
"""Manage the TuxCMDB Django web interface."""

from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import venv


BASE_DIR = Path(__file__).resolve().parent
VENV_DIR = BASE_DIR / ".venv"
WEBUI_DIR = BASE_DIR / "tuxcmdb-webui"
WEBUI_REQUIREMENTS = WEBUI_DIR / "requirements.txt"
WEBUI_SESSION_DIR = WEBUI_DIR / ".sessions"
RUNTIME_DIR = BASE_DIR / ".runtime"
PID_FILE = RUNTIME_DIR / "tuxcmdb-webui.pid"
LOG_FILE = RUNTIME_DIR / "tuxcmdb-webui.log"
RPM_VENV_DIRS = (Path("/opt/tuxcmdb-webui/venv"),)


def venv_python_path() -> Path:
    def python_from(venv_dir: Path) -> Path:
        if os.name == "nt":
            return venv_dir / "Scripts" / "python.exe"
        return venv_dir / "bin" / "python"

    for rpm_venv_dir in RPM_VENV_DIRS:
        candidate = python_from(rpm_venv_dir)
        if candidate.exists():
            return candidate

    return python_from(VENV_DIR)


def ensure_venv() -> Path:
    venv_python = venv_python_path()
    if not venv_python.exists():
        if any(venv_python == (rpm_dir / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")) for rpm_dir in RPM_VENV_DIRS):
            raise RuntimeError(f"RPM venv not found at {venv_python.parent.parent}; reinstall the tuxcmdb-webui RPMs")
        print(f"Creating virtual environment in {VENV_DIR}")
        venv.EnvBuilder(with_pip=True).create(str(VENV_DIR))
        venv_python = venv_python_path()

    current_python = Path(sys.executable).resolve()
    if current_python != venv_python.resolve():
        os.execv(
            str(venv_python),
            [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        )
    return venv_python


def ensure_dependencies() -> None:
    missing = []
    for module in ("django", "channels", "daphne", "requests", "sqlalchemy", "yaml", "werkzeug"):
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(module)

    if not missing:
        return

    if not WEBUI_REQUIREMENTS.exists():
        raise FileNotFoundError(f"Requirements file not found: {WEBUI_REQUIREMENTS}")

    print(f"Installing dependencies from {WEBUI_REQUIREMENTS}")
    subprocess.check_call([str(venv_python_path()), "-m", "pip", "install", "-r", str(WEBUI_REQUIREMENTS)])


def read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        PID_FILE.unlink(missing_ok=True)
        return None


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def ensure_runtime_dirs() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    WEBUI_SESSION_DIR.mkdir(parents=True, exist_ok=True)


def remove_stale_pid() -> None:
    pid = read_pid()
    if pid is None:
        return
    if not is_running(pid):
        PID_FILE.unlink(missing_ok=True)


def start_server(host: str, port: int) -> int:
    remove_stale_pid()
    pid = read_pid()
    if pid is not None and is_running(pid):
        print(f"TuxCMDB WebUI is already running with PID {pid}")
        return 1

    ensure_runtime_dirs()
    log_handle = LOG_FILE.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [
            str(venv_python_path()),
            "manage.py",
            "runserver",
            f"{host}:{port}",
            "--noreload",
        ],
        cwd=str(WEBUI_DIR),
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    time.sleep(1)
    return_code = process.poll()
    if return_code is not None:
        PID_FILE.unlink(missing_ok=True)
        print(f"Failed to start TuxCMDB WebUI. Check {LOG_FILE}")
        return return_code

    print(f"TuxCMDB WebUI started with PID {process.pid}")
    print(f"Log file: {LOG_FILE}")
    print(f"URL: http://{host}:{port}/")
    return 0


def stop_server(timeout: float = 10.0) -> int:
    remove_stale_pid()
    pid = read_pid()
    if pid is None:
        print("TuxCMDB WebUI is not running")
        return 0

    print(f"Stopping TuxCMDB WebUI PID {pid}")
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_running(pid):
            PID_FILE.unlink(missing_ok=True)
            print("TuxCMDB WebUI stopped")
            return 0
        time.sleep(0.2)

    print(f"PID {pid} did not stop after {timeout:.0f}s, sending SIGKILL")
    os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    return 0


def restart_server(host: str, port: int) -> int:
    stop_server()
    return start_server(host, port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start, stop, or restart the TuxCMDB Django web interface")
    parser.add_argument("command", choices=("start", "stop", "restart"))
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind the Django web UI to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind the Django web UI to")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    ensure_venv()
    ensure_dependencies()

    if args.command == "start":
        return start_server(args.host, args.port)
    if args.command == "stop":
        return stop_server()
    return restart_server(args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())