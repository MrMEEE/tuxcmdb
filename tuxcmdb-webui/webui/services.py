from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, func, inspect, select, text
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
    Column("name", String(120), nullable=True),
    Column("description", Text, nullable=True),
    Column("password_hash", String(255), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default=text("true")),
    Column("readonly", Boolean, nullable=False, server_default=text("false")),
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


class ServiceError(Exception):
    pass


@dataclass
class APIUserRecord:
    id: int
    username: str
    name: str | None
    description: str | None
    is_active: bool
    readonly: bool
    created_at: Any
    changed_at: Any


@dataclass
class AuditLogRecord:
    id: int
    actor_username: str
    entity_type: str
    entity_ref: str
    action: str
    details: str | None
    created_at: Any


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
    engine = create_db_engine(database_url())
    ensure_apiusers_profile_columns(engine)
    ensure_audit_log_table(engine)
    return engine


def ensure_apiusers_profile_columns(engine) -> None:
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


def ensure_audit_log_table(engine) -> None:
    metadata.create_all(engine, tables=[audit_log])


def log_audit_entry(
    conn,
    actor_username: str,
    entity_type: str,
    entity_ref: str,
    action: str,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        audit_log.insert().values(
            actor_username=actor_username,
            entity_type=entity_type,
            entity_ref=entity_ref,
            action=action,
            details=json.dumps(details, sort_keys=True, default=str) if details is not None else None,
        )
    )


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
                apiusers.c.name,
                apiusers.c.description,
                apiusers.c.is_active,
                apiusers.c.readonly,
                apiusers.c.created_at,
                apiusers.c.changed_at,
            ).order_by(apiusers.c.username)
        ).all()
    return [APIUserRecord(**row._mapping) for row in rows]


def list_audit_logs() -> list[AuditLogRecord]:
    engine = db_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                audit_log.c.id,
                audit_log.c.actor_username,
                audit_log.c.entity_type,
                audit_log.c.entity_ref,
                audit_log.c.action,
                audit_log.c.details,
                audit_log.c.created_at,
            ).order_by(audit_log.c.created_at.desc(), audit_log.c.id.desc())
        ).all()
    return [AuditLogRecord(**row._mapping) for row in rows]


def get_apiuser(user_id: int) -> APIUserRecord | None:
    engine = db_engine()
    with engine.connect() as conn:
        row = conn.execute(
            select(
                apiusers.c.id,
                apiusers.c.username,
                apiusers.c.name,
                apiusers.c.description,
                apiusers.c.is_active,
                apiusers.c.readonly,
                apiusers.c.created_at,
                apiusers.c.changed_at,
            ).where(apiusers.c.id == user_id)
        ).one_or_none()
    return APIUserRecord(**row._mapping) if row else None


def create_apiuser(
    actor_username: str,
    username: str,
    password: str,
    is_active: bool,
    readonly: bool,
    name: str | None,
    description: str | None,
) -> None:
    engine = db_engine()
    with engine.begin() as conn:
        existing = conn.execute(select(apiusers.c.id).where(apiusers.c.username == username)).scalar_one_or_none()
        if existing is not None:
            raise ServiceError("API user already exists")
        conn.execute(
            apiusers.insert().values(
                username=username,
                name=name,
                description=description,
                password_hash=generate_password_hash(password),
                is_active=is_active,
                readonly=readonly,
            )
        )
        log_audit_entry(
            conn,
            actor_username,
            "apiuser",
            username,
            "create",
            {
                "name": name,
                "description": description,
                "is_active": is_active,
                "readonly": readonly,
            },
        )


def update_apiuser(
    actor_username: str,
    user_id: int,
    username: str,
    password: str | None,
    is_active: bool,
    readonly: bool,
    name: str | None,
    description: str | None,
) -> None:
    values: dict[str, Any] = {
        "username": username,
        "name": name,
        "description": description,
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
        log_audit_entry(
            conn,
            actor_username,
            "apiuser",
            username,
            "update",
            {
                "name": name,
                "description": description,
                "is_active": is_active,
                "readonly": readonly,
                "password_changed": bool(password),
            },
        )


def delete_apiuser(actor_username: str, user_id: int) -> None:
    engine = db_engine()
    with engine.begin() as conn:
        username = conn.execute(select(apiusers.c.username).where(apiusers.c.id == user_id)).scalar_one_or_none()
        deleted = conn.execute(apiusers.delete().where(apiusers.c.id == user_id)).rowcount or 0
        if deleted == 0:
            raise ServiceError("API user not found")
        log_audit_entry(conn, actor_username, "apiuser", username or str(user_id), "delete")
