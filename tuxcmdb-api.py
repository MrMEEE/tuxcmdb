#!/usr/bin/env python3
"""Install script for tuxcmdb-api."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import subprocess
import sys
import venv


BASE_DIR = Path(__file__).resolve().parent
REQUIREMENTS_FILE = BASE_DIR / "tuxcmdb-api" / "requirements.txt"
VENV_DIR = BASE_DIR / ".venv"


def venv_python_path() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def ensure_venv_and_dependencies(requirements_file: Path, modules: tuple[str, ...]) -> None:
    venv_python = venv_python_path()

    if not venv_python.exists():
        print(f"Creating virtual environment in {VENV_DIR}")
        venv.EnvBuilder(with_pip=True).create(str(VENV_DIR))

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
    Column("password_hash", String(255), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default=text("true")),
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


def upsert_api_user(database_url: str, username: str, password: str) -> None:
    engine = create_db_engine(database_url)
    password_hash = generate_password_hash(password)

    with engine.begin() as conn:
        existing = conn.execute(
            select(apiusers.c.id).where(apiusers.c.username == username)
        ).scalar_one_or_none()

        if existing is None:
            conn.execute(
                apiusers.insert().values(
                    username=username,
                    password_hash=password_hash,
                    is_active=True,
                )
            )
            return

        conn.execute(
            apiusers.update()
            .where(apiusers.c.username == username)
            .values(password_hash=password_hash, is_active=True, changed_at=func.now())
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install tuxcmdb-api into an existing TuxCMDB database")
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
        "--run",
        action="store_true",
        help="Start tuxcmdb-api after installation/configuration",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

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
    ensure_assets_active_column(database_url)
    ensure_datatypes_table(database_url)
    print("Ensured apiusers and datatypes tables exist")

    if args.create_user:
        password = args.password or getpass.getpass(f"Password for API user '{args.create_user}': ")
        upsert_api_user(database_url, args.create_user, password)
        print(f"Created/updated API user '{args.create_user}'")

    save_api_config(Path(args.api_config).expanduser(), database_url, args.host, args.port)
    print(f"Saved API config to {Path(args.api_config).expanduser()}")

    if args.run:
        app_path = BASE_DIR / "tuxcmdb-api" / "app.py"
        print("Starting tuxcmdb-api")
        subprocess.check_call([sys.executable, str(app_path)])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
