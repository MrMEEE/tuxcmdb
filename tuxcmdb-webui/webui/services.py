from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, func, select, text
from werkzeug.security import check_password_hash, generate_password_hash
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tuxcmdb.db import create_db_engine

API_CONFIG_PATH = REPO_ROOT / "tuxcmdb-api" / "conf" / "api.yaml"
DB_CONFIG_PATH = REPO_ROOT / "conf" / "database.yaml"

metadata = MetaData()
apiusers = Table(
    "apiusers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(120), nullable=False, unique=True),
    Column("password_hash", String(255), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default=text("true")),
    Column("readonly", Boolean, nullable=False, server_default=text("false")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
)


class ServiceError(Exception):
    pass


@dataclass
class APIUserRecord:
    id: int
    username: str
    is_active: bool
    readonly: bool
    created_at: Any
    changed_at: Any


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return {}
    return data


def api_base_url() -> str:
    doc = load_yaml(API_CONFIG_PATH)
    api = doc.get("api", {})
    host = api.get("host", "127.0.0.1")
    port = api.get("port", 8080)
    return f"http://{host}:{port}"


def database_url() -> str:
    doc = load_yaml(DB_CONFIG_PATH)
    database = doc.get("database", {})
    url = database.get("url")
    if not isinstance(url, str) or not url:
        raise ServiceError("Database URL not configured")
    return url


def db_engine():
    return create_db_engine(database_url())


def authenticate_apiuser(username: str, password: str) -> dict[str, Any] | None:
    engine = db_engine()
    with engine.connect() as conn:
        row = conn.execute(
            select(
                apiusers.c.username,
                apiusers.c.password_hash,
                apiusers.c.is_active,
                apiusers.c.readonly,
            ).where(apiusers.c.username == username)
        ).one_or_none()

    if row is None or not row.is_active:
        return None
    if not check_password_hash(row.password_hash, password):
        return None
    return {"username": row.username, "readonly": row.readonly}


def api_request(username: str, password: str, method: str, path: str, *, params: dict[str, Any] | None = None, payload: Any = None) -> Any:
    url = f"{api_base_url()}{path}"
    response = requests.request(
        method=method,
        url=url,
        auth=(username, password),
        params=params,
        json=payload,
        timeout=20,
    )
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = response.text or f"HTTP {response.status_code}"
        raise ServiceError(str(detail))
    if response.content:
        return response.json()
    return None


def list_apiusers() -> list[APIUserRecord]:
    engine = db_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                apiusers.c.id,
                apiusers.c.username,
                apiusers.c.is_active,
                apiusers.c.readonly,
                apiusers.c.created_at,
                apiusers.c.changed_at,
            ).order_by(apiusers.c.username)
        ).all()
    return [APIUserRecord(**row._mapping) for row in rows]


def get_apiuser(user_id: int) -> APIUserRecord | None:
    engine = db_engine()
    with engine.connect() as conn:
        row = conn.execute(
            select(
                apiusers.c.id,
                apiusers.c.username,
                apiusers.c.is_active,
                apiusers.c.readonly,
                apiusers.c.created_at,
                apiusers.c.changed_at,
            ).where(apiusers.c.id == user_id)
        ).one_or_none()
    return APIUserRecord(**row._mapping) if row else None


def create_apiuser(username: str, password: str, is_active: bool, readonly: bool) -> None:
    engine = db_engine()
    with engine.begin() as conn:
        existing = conn.execute(select(apiusers.c.id).where(apiusers.c.username == username)).scalar_one_or_none()
        if existing is not None:
            raise ServiceError("API user already exists")
        conn.execute(
            apiusers.insert().values(
                username=username,
                password_hash=generate_password_hash(password),
                is_active=is_active,
                readonly=readonly,
            )
        )


def update_apiuser(user_id: int, username: str, password: str | None, is_active: bool, readonly: bool) -> None:
    values: dict[str, Any] = {
        "username": username,
        "is_active": is_active,
        "readonly": readonly,
        "changed_at": func.now(),
    }
    if password:
        values["password_hash"] = generate_password_hash(password)
    engine = db_engine()
    with engine.begin() as conn:
        duplicate = conn.execute(
            select(apiusers.c.id).where(apiusers.c.username == username, apiusers.c.id != user_id)
        ).scalar_one_or_none()
        if duplicate is not None:
            raise ServiceError("API user already exists")
        updated = conn.execute(apiusers.update().where(apiusers.c.id == user_id).values(**values)).rowcount or 0
        if updated == 0:
            raise ServiceError("API user not found")


def delete_apiuser(user_id: int) -> None:
    engine = db_engine()
    with engine.begin() as conn:
        deleted = conn.execute(apiusers.delete().where(apiusers.c.id == user_id)).rowcount or 0
        if deleted == 0:
            raise ServiceError("API user not found")
