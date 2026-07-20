#!/usr/bin/env python3
"""Install script for tuxcmdb-api."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import venv


BASE_DIR = Path(__file__).resolve().parent
REQUIREMENTS_FILE = BASE_DIR / "tuxcmdb-api" / "requirements.txt"
VENV_DIR = BASE_DIR / ".venv"
RPM_VENV_DIRS = (Path("/opt/tuxcmdb-api/venv"),)
RUNTIME_DIR = BASE_DIR / ".runtime"
PID_FILE = RUNTIME_DIR / "tuxcmdb-api.pid"
LOG_FILE = RUNTIME_DIR / "tuxcmdb-api.log"


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


def ensure_venv_and_dependencies(requirements_file: Path, modules: tuple[str, ...]) -> None:
    venv_python = venv_python_path()

    if not venv_python.exists():
        if any(venv_python == (rpm_dir / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")) for rpm_dir in RPM_VENV_DIRS):
            raise RuntimeError(f"RPM venv not found at {venv_python.parent.parent}; reinstall the tuxcmdb-api RPMs")
        print(f"Creating virtual environment in {VENV_DIR}")
        venv.EnvBuilder(with_pip=True).create(str(VENV_DIR))
        venv_python = venv_python_path()

    import_check = "\n".join([f"import {module}" for module in modules])
    check_result = subprocess.run(
        [str(venv_python), "-c", import_check],
        capture_output=True,
        text=True,
    )

    if check_result.returncode != 0:
        if not requirements_file.exists():
            raise FileNotFoundError(f"Requirements file not found: {requirements_file}")
        print(f"Installing dependencies from {requirements_file}")
        subprocess.check_call(
            [str(venv_python), "-m", "pip", "install", "-r", str(requirements_file)]
        )

    current_python = Path(sys.executable).resolve()
    if current_python != venv_python.resolve():
        os.execv(
            str(venv_python),
            [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        )


ensure_venv_and_dependencies(
    REQUIREMENTS_FILE,
    ("sqlalchemy", "yaml", "werkzeug", "fastapi", "uvicorn"),
)

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
    inspect,
    select,
    text,
)
from tuxcmdb.db import create_db_engine
from werkzeug.security import generate_password_hash
import yaml


DEFAULT_DB_CONFIG = BASE_DIR / "conf" / "database.yaml"
DEFAULT_API_CONFIG = BASE_DIR / "tuxcmdb-api" / "conf" / "api.yaml"

metadata = MetaData()
apiusers = Table(
    "apiusers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(120), nullable=False, unique=True),
    Column("name", String(120), nullable=True),
    Column("description", Text, nullable=True),
    Column("password_hash", String(255), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default=text("true")),
    Column("readonly", Boolean, nullable=False, server_default=text("false")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
)

datatypes = Table(
    "datatypes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(32), nullable=False, unique=True),
    Column("description", Text, nullable=True),
    Column("regex_pattern", Text, nullable=True),
    Column("builtin_validator", String(32), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("actor_username", String(120), nullable=False),
    Column("entity_type", String(64), nullable=False),
    Column("entity_ref", String(255), nullable=False),
    Column("action", String(64), nullable=False),
    Column("details", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


def load_database_url_from_config(config_path: Path) -> str | None:
    if not config_path.exists():
        return None
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return None
    db_data = data.get("database")
    if not isinstance(db_data, dict):
        return None
    url = db_data.get("url")
    return str(url) if isinstance(url, str) and url else None


def save_api_config(config_path: Path, database_url: str, host: str, port: int) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "api": {
            "database_url": database_url,
            "host": host,
            "port": port,
        }
    }
    with config_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False)


def ensure_apiusers_table(database_url: str) -> None:
    engine = create_db_engine(database_url)
    metadata.create_all(engine, tables=[apiusers])


def ensure_apiusers_readonly_column(database_url: str) -> None:
    engine = create_db_engine(database_url)
    inspector = inspect(engine)
    if "apiusers" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("apiusers")}
    if "readonly" in columns:
        return

    dialect_name = engine.dialect.name
    with engine.begin() as conn:
        if dialect_name == "sqlite":
            conn.execute(text("ALTER TABLE apiusers ADD COLUMN readonly BOOLEAN NOT NULL DEFAULT 0"))
        else:
            conn.execute(text("ALTER TABLE apiusers ADD COLUMN readonly BOOLEAN NOT NULL DEFAULT false"))
        conn.execute(text("UPDATE apiusers SET readonly = false WHERE readonly IS NULL"))


def ensure_apiusers_profile_columns(database_url: str) -> None:
    engine = create_db_engine(database_url)
    inspector = inspect(engine)
    if "apiusers" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("apiusers")}
    with engine.begin() as conn:
        if "name" not in columns:
            conn.execute(text("ALTER TABLE apiusers ADD COLUMN name VARCHAR(120)"))
        if "description" not in columns:
            conn.execute(text("ALTER TABLE apiusers ADD COLUMN description TEXT"))
        conn.execute(text("UPDATE apiusers SET name = username WHERE name IS NULL OR name = ''"))


def ensure_datatypes_table(database_url: str) -> None:
    engine = create_db_engine(database_url)
    metadata.create_all(engine, tables=[datatypes])

    defaults = [
        {
            "name": "string",
            "description": "Any string (no validation)",
            "regex_pattern": None,
            "builtin_validator": None,
        },
        {
            "name": "integer",
            "description": "Signed integer validated by builtin parser",
            "regex_pattern": None,
            "builtin_validator": "integer",
        },
        {
            "name": "numeric",
            "description": "Signed integer or decimal number",
            "regex_pattern": r"^-?\\d+(?:\\.\\d+)?$",
            "builtin_validator": None,
        },
        {
            "name": "ipv4",
            "description": "IPv4 address validated with Python ipaddress",
            "regex_pattern": None,
            "builtin_validator": "ipv4",
        },
        {
            "name": "ipv6",
            "description": "IPv6 address validated with Python ipaddress",
            "regex_pattern": None,
            "builtin_validator": "ipv6",
        },
        {
            "name": "subnet",
            "description": "IPv4/IPv6 subnet in CIDR notation, for example 10.0.0.0/24",
            "regex_pattern": None,
            "builtin_validator": "subnet",
        },
        {
            "name": "boolean",
            "description": "Boolean value: true/false, 1/0, yes/no, on/off",
            "regex_pattern": None,
            "builtin_validator": "boolean",
        },
    ]

    with engine.begin() as conn:
        for row in defaults:
            existing = conn.execute(
                select(datatypes.c.id).where(datatypes.c.name == row["name"])
            ).scalar_one_or_none()
            if existing is None:
                conn.execute(datatypes.insert().values(**row))


def ensure_audit_log_table(database_url: str) -> None:
    engine = create_db_engine(database_url)
    metadata.create_all(engine, tables=[audit_log])


def ensure_assets_active_column(database_url: str) -> None:
    engine = create_db_engine(database_url)
    inspector = inspect(engine)
    if "assets" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("assets")}
    if "active" in columns:
        return

    dialect_name = engine.dialect.name
    with engine.begin() as conn:
        if dialect_name == "sqlite":
            conn.execute(text("ALTER TABLE assets ADD COLUMN active BOOLEAN NOT NULL DEFAULT 1"))
        else:
            conn.execute(text("ALTER TABLE assets ADD COLUMN active BOOLEAN NOT NULL DEFAULT true"))
        conn.execute(text("UPDATE assets SET active = true WHERE active IS NULL"))


def upsert_api_user(
    database_url: str,
    username: str,
    password: str,
    readonly: bool = False,
    name: str | None = None,
    description: str | None = None,
) -> None:
    engine = create_db_engine(database_url)
    password_hash = generate_password_hash(password)
    display_name = name or username

    with engine.begin() as conn:
        existing = conn.execute(
            select(apiusers.c.id).where(apiusers.c.username == username)
        ).scalar_one_or_none()

        if existing is None:
            conn.execute(
                apiusers.insert().values(
                    username=username,
                    name=display_name,
                    description=description,
                    password_hash=password_hash,
                    is_active=True,
                    readonly=readonly,
                )
            )
            return

        update_values = {
            "password_hash": password_hash,
            "is_active": True,
            "readonly": readonly,
            "changed_at": func.now(),
        }
        if name is not None:
            update_values["name"] = name
        if description is not None:
            update_values["description"] = description
        conn.execute(
            apiusers.update()
            .where(apiusers.c.username == username)
            .values(**update_values)
        )


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


def ensure_runtime_dir() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def remove_stale_pid() -> None:
    pid = read_pid()
    if pid is None:
        return
    if not is_running(pid):
        PID_FILE.unlink(missing_ok=True)


def start_server(timeout: float = 1.0) -> int:
    remove_stale_pid()
    pid = read_pid()
    if pid is not None and is_running(pid):
        print(f"tuxcmdb-api is already running with PID {pid}")
        return 1

    ensure_runtime_dir()
    app_path = BASE_DIR / "tuxcmdb-api" / "app.py"
    log_handle = LOG_FILE.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [str(venv_python_path()), str(app_path)],
        cwd=str(BASE_DIR),
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    time.sleep(timeout)
    return_code = process.poll()
    if return_code is not None:
        PID_FILE.unlink(missing_ok=True)
        print(f"Failed to start tuxcmdb-api. Check {LOG_FILE}")
        return return_code

    print(f"tuxcmdb-api started with PID {process.pid}")
    print(f"Log file: {LOG_FILE}")
    return 0


def stop_server(timeout: float = 10.0) -> int:
    remove_stale_pid()
    pid = read_pid()
    if pid is None:
        print("tuxcmdb-api is not running")
        return 0

    print(f"Stopping tuxcmdb-api PID {pid}")
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_running(pid):
            PID_FILE.unlink(missing_ok=True)
            print("tuxcmdb-api stopped")
            return 0
        time.sleep(0.2)

    print(f"PID {pid} did not stop after {timeout:.0f}s, sending SIGKILL")
    os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    return 0


def restart_server() -> int:
    stop_server()
    return start_server()


def configure_api(args: argparse.Namespace) -> int:
    database_url = args.database_url
    if not database_url:
        database_url = load_database_url_from_config(Path(args.database_config).expanduser())

    if not database_url:
        print(
            "Error: could not resolve database URL. Use --database-url or provide a valid --database-config.",
            file=sys.stderr,
        )
        return 2

    ensure_apiusers_table(database_url)
    ensure_apiusers_readonly_column(database_url)
    ensure_apiusers_profile_columns(database_url)
    ensure_assets_active_column(database_url)
    ensure_datatypes_table(database_url)
    ensure_audit_log_table(database_url)
    print("Ensured apiusers and datatypes tables exist")

    if args.create_user:
        password = args.password or getpass.getpass(f"Password for API user '{args.create_user}': ")
        upsert_api_user(
            database_url,
            args.create_user,
            password,
            readonly=args.readonly,
            name=args.name,
            description=args.description,
        )
        print(f"Created/updated API user '{args.create_user}'")

    save_api_config(Path(args.api_config).expanduser(), database_url, args.host, args.port)
    print(f"Saved API config to {Path(args.api_config).expanduser()}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure and control tuxcmdb-api (configure/start/stop/restart)"
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("configure", "start", "stop", "restart"),
        default="configure",
        help="Lifecycle command (default: configure)",
    )
    parser.add_argument("--database-url", help="Explicit database URL for the existing tuxcmdb database")
    parser.add_argument(
        "--database-config",
        default=str(DEFAULT_DB_CONFIG),
        help="Path to tuxcmdb database config file (used when --database-url is not provided)",
    )
    parser.add_argument(
        "--api-config",
        default=str(DEFAULT_API_CONFIG),
        help="Path where tuxcmdb-api config YAML should be written",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for the API to bind to (saved in api config)",
    )
    parser.add_argument(
        "--port",
        default=8080,
        type=int,
        help="Port for the API to bind to (saved in api config)",
    )
    parser.add_argument(
        "--create-user",
        help="Create or update an API user during install",
    )
    parser.add_argument(
        "--password",
        help="Password for --create-user. If omitted, prompt securely.",
    )
    parser.add_argument(
        "--readonly",
        action="store_true",
        help="Create or update the API user with readonly access",
    )
    parser.add_argument(
        "--name",
        help="Display name for --create-user (defaults to username)",
    )
    parser.add_argument(
        "--description",
        help="Description for --create-user",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.command == "stop":
        return stop_server()

    configure_result = configure_api(args)
    if configure_result != 0:
        return configure_result

    if args.command == "start":
        return start_server()
    if args.command == "restart":
        return restart_server()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
