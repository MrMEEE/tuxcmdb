"""Container-only configuration and serialized API schema initialization."""

from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
import sys
import time

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError
import yaml

ROOT = Path(__file__).resolve().parents[2]
API_CONFIG = ROOT / "tuxcmdb-api" / "conf" / "api.yaml"
logger = logging.getLogger(__name__)


def database_url() -> str:
    backend = os.environ["DB_BACKEND"]
    drivers = {"mariadb": "mysql+pymysql", "postgresql": "postgresql+psycopg"}
    return URL.create(
        drivers[backend],
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        database=os.environ["DB_NAME"],
    ).render_as_string(hide_password=False)


def configure() -> None:
    component = os.environ["TUXCMDB_COMPONENT"]
    if component not in {"api", "webui"}:
        raise ValueError(f"Unsupported TUXCMDB_COMPONENT: {component}")
    url = database_url()
    documents = {
        ROOT / "conf" / "database.yaml": {"database": {"url": url}},
        API_CONFIG: {
            "api": {
                "database_url": url,
                "host": "0.0.0.0" if component == "api" else "tuxcmdb-api",
                "port": 8080,
            }
        },
    }
    for path, document in documents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(document), encoding="utf-8")
        path.chmod(0o600)


def migrate() -> None:
    url = database_url()
    engine = create_engine(url)
    deadline = time.monotonic() + 120
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            break
        except OperationalError:
            if time.monotonic() >= deadline:
                raise
            logger.warning("Database not ready; retrying in 3 seconds")
            time.sleep(3)
    engine.dispose()

    os.environ["DATABASE_URL"] = url
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tuxcmdb-api"))
    api = importlib.import_module("app")
    # API startup reconciles the default datatypes/attributes and extension tables.
    api.create_app(API_CONFIG)
    engine = api.create_db_engine(url)
    username = os.environ["TUXCMDB_ADMIN_USER"]
    with engine.connect() as conn:
        existing = conn.execute(
            select(api.apiusers.c.id).where(api.apiusers.c.username == username)
        ).scalar_one_or_none()
    engine.dispose()
    if existing is None:
        api.create_apiuser_cli(
            config_path=API_CONFIG,
            username=username,
            password=os.environ["TUXCMDB_ADMIN_PASSWORD"],
            name="Initial administrator",
            description="Bootstrapped by the OpenShift deployment",
            is_active=True,
            readonly=False,
        )
        logger.info("Created initial administrator")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if sys.argv[1:] == ["configure"]:
        configure()
    elif sys.argv[1:] == ["migrate"]:
        migrate()
    else:
        raise SystemExit("Usage: runtime.py configure|migrate")
