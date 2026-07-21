from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, func, inspect, select, text
from werkzeug.security import generate_password_hash
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tuxcmdb.db import create_db_engine

API_CONFIG_CANDIDATES = (
    REPO_ROOT / "tuxcmdb-api" / "conf" / "api.yaml",             # development
    Path("/opt/tuxcmdb-api/tuxcmdb-api/conf/api.yaml"),            # production
)
DB_CONFIG_CANDIDATES = (
    REPO_ROOT / "conf" / "database.yaml",                         # development
    Path("/opt/tuxcmdb/conf/database.yaml"),                       # production
)

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


@dataclass
class LDAPSourceRecord:
    id: int
    name: str
    hostname: str
    port: int
    protocol: str
    verify_certs: bool
    server_type: str
    bind_dn: str | None
    bind_password_set: bool
    base_dn: str
    group_base_dn: str | None
    group_membership: str
    ldap_filter: str
    attr_username: str
    attr_first_name: str
    attr_last_name: str
    attr_email: str
    is_active: bool
    created_at: Any
    changed_at: Any


@dataclass
class LDAPUserAccessRecord:
    id: int
    username: str
    source_id: int | None
    source_name: str | None
    readonly: bool
    is_active: bool
    last_login_at: Any
    created_at: Any
    changed_at: Any


@dataclass
class LDAPGroupRoleMappingRecord:
    id: int
    source_id: int
    source_name: str | None
    group_name: str
    readonly: bool
    is_active: bool
    created_at: Any
    changed_at: Any


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return {}
    return data


def load_first_yaml(candidates: tuple[Path, ...]) -> dict[str, Any]:
    for candidate in candidates:
        data = load_yaml(candidate)
        if data:
            return data
    return {}


def api_base_url() -> str:
    doc = load_first_yaml(API_CONFIG_CANDIDATES)
    api = doc.get("api", {})
    host = api.get("host", "127.0.0.1")
    port = api.get("port", 8080)
    return f"http://{host}:{port}"


def database_url() -> str:
    doc = load_first_yaml(DB_CONFIG_CANDIDATES)
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
    try:
        result = api_request(username, password, "GET", "/ok")
    except ServiceError:
        return None

    if not isinstance(result, dict):
        return None

    status_value = str(result.get("status", "")).lower()
    if status_value != "ok":
        return None

    api_username = result.get("user")
    readonly = bool(result.get("readonly", False))
    return {
        "username": str(api_username) if isinstance(api_username, str) and api_username else username,
        "readonly": readonly,
    }


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


def list_apiusers(username: str, password: str) -> list[APIUserRecord]:
    rows = api_request(username, password, "GET", "/v1/apiusers")
    if not isinstance(rows, list):
        return []
    out: list[APIUserRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            APIUserRecord(
                id=int(row.get("id", 0)),
                username=str(row.get("username", "")),
                name=row.get("name"),
                description=row.get("description"),
                is_active=bool(row.get("is_active", False)),
                readonly=bool(row.get("readonly", False)),
                created_at=row.get("created_at"),
                changed_at=row.get("changed_at"),
            )
        )
    return out


def list_audit_logs(username: str, password: str) -> list[AuditLogRecord]:
    rows = api_request(username, password, "GET", "/v1/audit")
    if not isinstance(rows, list):
        return []
    out: list[AuditLogRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            AuditLogRecord(
                id=int(row.get("id", 0)),
                actor_username=str(row.get("actor_username", "")),
                entity_type=str(row.get("entity_type", "")),
                entity_ref=str(row.get("entity_ref", "")),
                action=str(row.get("action", "")),
                details=row.get("details"),
                created_at=row.get("created_at"),
            )
        )
    return out


def latest_audit_marker() -> int:
    # Kept for backward compatibility with consumers; no DB polling in API-only mode.
    return 0


def get_apiuser(username: str, password: str, user_id: int) -> APIUserRecord | None:
    try:
        row = api_request(username, password, "GET", f"/v1/apiusers/{user_id}")
    except ServiceError as exc:
        if "not found" in str(exc).lower():
            return None
        raise
    if not isinstance(row, dict):
        return None
    return APIUserRecord(
        id=int(row.get("id", 0)),
        username=str(row.get("username", "")),
        name=row.get("name"),
        description=row.get("description"),
        is_active=bool(row.get("is_active", False)),
        readonly=bool(row.get("readonly", False)),
        created_at=row.get("created_at"),
        changed_at=row.get("changed_at"),
    )


def create_apiuser(
    api_username: str,
    api_password: str,
    username: str,
    password: str,
    is_active: bool,
    readonly: bool,
    name: str | None,
    description: str | None,
) -> None:
    api_request(
        api_username,
        api_password,
        "POST",
        "/v1/apiusers",
        payload={
            "username": username,
            "password": password,
            "is_active": is_active,
            "readonly": readonly,
            "name": name,
            "description": description,
        },
    )


def update_apiuser(
    api_username: str,
    api_password: str,
    user_id: int,
    username: str,
    password: str | None,
    is_active: bool,
    readonly: bool,
    name: str | None,
    description: str | None,
) -> None:
    payload: dict[str, Any] = {
        "username": username,
        "is_active": is_active,
        "readonly": readonly,
        "name": name,
        "description": description,
    }
    if password:
        payload["password"] = password

    api_request(api_username, api_password, "PATCH", f"/v1/apiusers/{user_id}", payload=payload)


def delete_apiuser(api_username: str, api_password: str, user_id: int) -> None:
    api_request(api_username, api_password, "DELETE", f"/v1/apiusers/{user_id}")


def list_ldap_sources(api_username: str, api_password: str) -> list[LDAPSourceRecord]:
    rows = api_request(api_username, api_password, "GET", "/v1/ldap/sources")
    if not isinstance(rows, list):
        return []
    out: list[LDAPSourceRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            LDAPSourceRecord(
                id=int(row.get("id", 0)),
                name=str(row.get("name", "")),
                hostname=str(row.get("hostname", "")),
                port=int(row.get("port", 389)),
                protocol=str(row.get("protocol", "ldap")),
                verify_certs=bool(row.get("verify_certs", True)),
                server_type=str(row.get("server_type", "ad")),
                bind_dn=row.get("bind_dn"),
                bind_password_set=bool(row.get("bind_password_set", False)),
                base_dn=str(row.get("base_dn", "")),
                group_base_dn=row.get("group_base_dn"),
                group_membership=str(row.get("group_membership", "ad")),
                ldap_filter=str(row.get("ldap_filter", "")),
                attr_username=str(row.get("attr_username", "")),
                attr_first_name=str(row.get("attr_first_name", "")),
                attr_last_name=str(row.get("attr_last_name", "")),
                attr_email=str(row.get("attr_email", "")),
                is_active=bool(row.get("is_active", False)),
                created_at=row.get("created_at"),
                changed_at=row.get("changed_at"),
            )
        )
    return out


def get_ldap_source(api_username: str, api_password: str, source_id: int) -> LDAPSourceRecord | None:
    try:
        row = api_request(api_username, api_password, "GET", f"/v1/ldap/sources/{source_id}")
    except ServiceError as exc:
        if "not found" in str(exc).lower():
            return None
        raise
    if not isinstance(row, dict):
        return None
    return LDAPSourceRecord(
        id=int(row.get("id", 0)),
        name=str(row.get("name", "")),
        hostname=str(row.get("hostname", "")),
        port=int(row.get("port", 389)),
        protocol=str(row.get("protocol", "ldap")),
        verify_certs=bool(row.get("verify_certs", True)),
        server_type=str(row.get("server_type", "ad")),
        bind_dn=row.get("bind_dn"),
        bind_password_set=bool(row.get("bind_password_set", False)),
        base_dn=str(row.get("base_dn", "")),
        group_base_dn=row.get("group_base_dn"),
        group_membership=str(row.get("group_membership", "ad")),
        ldap_filter=str(row.get("ldap_filter", "")),
        attr_username=str(row.get("attr_username", "")),
        attr_first_name=str(row.get("attr_first_name", "")),
        attr_last_name=str(row.get("attr_last_name", "")),
        attr_email=str(row.get("attr_email", "")),
        is_active=bool(row.get("is_active", False)),
        created_at=row.get("created_at"),
        changed_at=row.get("changed_at"),
    )


def create_ldap_source(api_username: str, api_password: str, payload: dict[str, Any]) -> None:
    api_request(api_username, api_password, "POST", "/v1/ldap/sources", payload=payload)


def update_ldap_source(api_username: str, api_password: str, source_id: int, payload: dict[str, Any]) -> None:
    api_request(api_username, api_password, "PATCH", f"/v1/ldap/sources/{source_id}", payload=payload)


def delete_ldap_source(api_username: str, api_password: str, source_id: int) -> None:
    api_request(api_username, api_password, "DELETE", f"/v1/ldap/sources/{source_id}")


def list_ldap_user_access(api_username: str, api_password: str) -> list[LDAPUserAccessRecord]:
    rows = api_request(api_username, api_password, "GET", "/v1/ldap/users")
    if not isinstance(rows, list):
        return []
    out: list[LDAPUserAccessRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            LDAPUserAccessRecord(
                id=int(row.get("id", 0)),
                username=str(row.get("username", "")),
                source_id=int(row["source_id"]) if row.get("source_id") is not None else None,
                source_name=row.get("source_name"),
                readonly=bool(row.get("readonly", True)),
                is_active=bool(row.get("is_active", True)),
                last_login_at=row.get("last_login_at"),
                created_at=row.get("created_at"),
                changed_at=row.get("changed_at"),
            )
        )
    return out


def update_ldap_user_access(api_username: str, api_password: str, username: str, payload: dict[str, Any]) -> None:
    api_request(api_username, api_password, "PATCH", f"/v1/ldap/users/{username}", payload=payload)


def delete_ldap_user_access(api_username: str, api_password: str, username: str) -> None:
    api_request(api_username, api_password, "DELETE", f"/v1/ldap/users/{username}")


def list_ldap_group_role_mappings(
    api_username: str,
    api_password: str,
    source_id: int | None = None,
) -> list[LDAPGroupRoleMappingRecord]:
    params = {"source_id": source_id} if source_id is not None else None
    rows = api_request(api_username, api_password, "GET", "/v1/ldap/group-mappings", params=params)
    if not isinstance(rows, list):
        return []
    out: list[LDAPGroupRoleMappingRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            LDAPGroupRoleMappingRecord(
                id=int(row.get("id", 0)),
                source_id=int(row.get("source_id", 0)),
                source_name=row.get("source_name"),
                group_name=str(row.get("group_name", "")),
                readonly=bool(row.get("readonly", True)),
                is_active=bool(row.get("is_active", True)),
                created_at=row.get("created_at"),
                changed_at=row.get("changed_at"),
            )
        )
    return out


def create_ldap_group_role_mapping(api_username: str, api_password: str, payload: dict[str, Any]) -> None:
    api_request(api_username, api_password, "POST", "/v1/ldap/group-mappings", payload=payload)


def update_ldap_group_role_mapping(api_username: str, api_password: str, mapping_id: int, payload: dict[str, Any]) -> None:
    api_request(api_username, api_password, "PATCH", f"/v1/ldap/group-mappings/{mapping_id}", payload=payload)


def delete_ldap_group_role_mapping(api_username: str, api_password: str, mapping_id: int) -> None:
    api_request(api_username, api_password, "DELETE", f"/v1/ldap/group-mappings/{mapping_id}")
