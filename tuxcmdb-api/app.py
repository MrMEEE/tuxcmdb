#!/usr/bin/env python3
"""Authenticated FastAPI service for TuxCMDB."""

from __future__ import annotations

from datetime import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tuxcmdb.db import create_db_engine
from werkzeug.security import check_password_hash, generate_password_hash
import uvicorn
import yaml


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_API_CONFIG = BASE_DIR / "conf" / "api.yaml"

# Candidate paths for the tuxcmdb core database config (written by 'tuxcmdb setup').
# The first existing file that contains a valid URL wins.
TUXCMDB_DB_CONFIG_CANDIDATES = (
    BASE_DIR.parent / "conf" / "database.yaml",  # development (repo root)
    Path("/opt/tuxcmdb/conf/database.yaml"),      # production installation
)

APPROVAL_NOT_PENDING = 0
APPROVAL_PENDING = 1
APPROVAL_APPROVED = 2
APPROVAL_REJECTED = 3

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

assets = Table(
    "assets",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("assetname", String(255), nullable=False, unique=True),
    Column("operatingsystem_id", Integer, ForeignKey("operatingsystems.id", ondelete="SET NULL"), nullable=True),
    Column("approved", Integer, nullable=False, server_default=text("0")),
    Column("systempass_hash", String(255), nullable=True),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
)

attributes = Table(
    "attributes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(120), nullable=False, unique=True),
    Column("description", Text, nullable=True),
    Column("data_type", String(32), ForeignKey("datatypes.name"), nullable=False),
    Column("allow_multiple", Boolean, nullable=False, server_default=text("false")),
    Column("immutable", Boolean, nullable=False, server_default=text("false")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
)

operatingsystems = Table(
    "operatingsystems",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(120), nullable=False, unique=True),
    Column("description", Text, nullable=True),
    Column("aliases", Text, nullable=False, server_default=text("'[]'")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("changed_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
)

attribute_fetchmethods = Table(
    "attribute_fetchmethods",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("attribute_id", Integer, ForeignKey("attributes.id", ondelete="CASCADE"), nullable=False),
    Column("command", Text, nullable=False),
)

attribute_fetchmethod_operatingsystems = Table(
    "attribute_fetchmethod_operatingsystems",
    metadata,
    Column("fetchmethod_id", Integer, ForeignKey("attribute_fetchmethods.id", ondelete="CASCADE"), primary_key=True),
    Column("operatingsystem_id", Integer, ForeignKey("operatingsystems.id", ondelete="CASCADE"), primary_key=True),
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

assignments = Table(
    "assignments",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("asset_id", Integer, nullable=False),
    Column("attribute_id", Integer, nullable=False),
    Column("value", Text, nullable=True),
    Column("assigned", Boolean, nullable=False, server_default=text("true")),
    Column("assigned_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
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

security = HTTPBasic()


class HealthResponse(BaseModel):
    status: str


class OkResponse(BaseModel):
    status: str
    user: str
    readonly: bool


class MessageResponse(BaseModel):
    status: str
    message: str


class AuthenticatedUser(BaseModel):
    username: str
    readonly: bool


class AttributeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    data_type: str = Field(default="string", min_length=1, max_length=32)
    description: str | None = None
    allow_multiple: bool = False
    immutable: bool = False
    fetchmethods: list["AttributeFetchMethodIn"] = Field(default_factory=list)


class AttributeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    data_type: str | None = Field(default=None, min_length=1, max_length=32)
    description: str | None = None
    allow_multiple: bool | None = None
    immutable: bool | None = None
    fetchmethods: list["AttributeFetchMethodIn"] | None = None


class AttributeFetchMethodIn(BaseModel):
    command: str = Field(min_length=1)
    supported_operatingsystems: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_supported_operatingsystems(self) -> "AttributeFetchMethodIn":
        if not self.supported_operatingsystems:
            raise ValueError("Each fetch method must include one or more supported operating systems")
        return self


class AttributeFetchMethodOut(BaseModel):
    command: str
    supported_operatingsystems: list[str] = Field(default_factory=list)


class AttributeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    data_type: str
    allow_multiple: bool
    immutable: bool
    description: str | None
    fetchmethods: list[AttributeFetchMethodOut] = Field(default_factory=list)
    created_at: datetime
    changed_at: datetime


class OperatingSystemOut(BaseModel):
    id: int
    name: str
    description: str | None
    aliases: list[str] = Field(default_factory=list)
    created_at: datetime
    changed_at: datetime


class OperatingSystemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)


class OperatingSystemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    aliases: list[str] | None = None


class DatatypeOut(BaseModel):
    id: int
    name: str
    description: str | None
    regex_pattern: str | None
    builtin_validator: str | None
    created_at: datetime
    changed_at: datetime


class DatatypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    description: str | None = None
    regex_pattern: str | None = None
    builtin_validator: str | None = Field(default=None, max_length=32)


class AssetCreate(BaseModel):
    assetname: str = Field(min_length=1, max_length=255)


class AssetUpdate(BaseModel):
    assetname: str | None = Field(default=None, min_length=1, max_length=255)


class AssetAssignRequest(BaseModel):
    attribute_id: int | None = None
    attribute_name: str | None = Field(default=None, min_length=1, max_length=120)
    value: str | None = None

    @model_validator(mode="after")
    def validate_selector(self) -> "AssetAssignRequest":
        has_id = self.attribute_id is not None
        has_name = self.attribute_name is not None
        if has_id == has_name:
            raise ValueError("Provide exactly one of attribute_id or attribute_name")

        if self.attribute_name is not None:
            normalized = self.attribute_name.strip().lower()
            if not normalized:
                raise ValueError("attribute_name must not be empty")
            self.attribute_name = normalized

        return self


def parse_asset_assign_payload(payload: dict[str, Any]) -> tuple[int | None, str | None, str | None]:
    reserved = {"attribute_id", "attribute_name", "value"}
    keys = set(payload.keys())

    if keys & reserved:
        extra_keys = keys - reserved
        if extra_keys:
            raise HTTPException(
                status_code=400,
                detail="When using attribute_id/attribute_name format, only attribute_id, attribute_name, and value are allowed",
            )

        try:
            request = AssetAssignRequest(**payload)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return request.attribute_id, request.attribute_name, request.value

    if len(payload) != 1:
        raise HTTPException(
            status_code=400,
            detail="Provide either {attribute_name, value} style payload or a single-key shorthand payload",
        )

    attribute_name, raw_value = next(iter(payload.items()))
    normalized_name = attribute_name.strip().lower()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Attribute name must not be empty")

    if raw_value is None:
        value: str | None = None
    elif isinstance(raw_value, str):
        value = raw_value
    else:
        value = str(raw_value)

    return None, normalized_name, value


def resolve_asset_ref(conn: Connection, asset_ref: str) -> Any:
    asset_ref = asset_ref.strip()

    row = None
    if asset_ref.isdigit():
        row = conn.execute(
            select(assets.c.id, assets.c.active).where(assets.c.id == int(asset_ref))
        ).one_or_none()
        if row is not None:
            return row

    normalized_assetname = normalize_assetname(asset_ref)
    row = conn.execute(
        select(assets.c.id, assets.c.active).where(assets.c.assetname == normalized_assetname)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return row


def resolve_attribute_ref(conn: Connection, attribute_ref: str) -> Any:
    attribute_ref = attribute_ref.strip()

    row = None
    if attribute_ref.isdigit():
        row = conn.execute(
            select(attributes.c.id, attributes.c.name, attributes.c.allow_multiple).where(attributes.c.id == int(attribute_ref))
        ).one_or_none()
        if row is not None:
            return row

    normalized_name = attribute_ref.lower()
    row = conn.execute(
        select(attributes.c.id, attributes.c.name, attributes.c.allow_multiple).where(attributes.c.name == normalized_name)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Attribute not found")
    return row


class AssignedAttributeOut(BaseModel):
    attribute_id: int
    name: str
    value: str | None
    assigned_at: datetime
    history_count: int = 1


class AssignmentHistoryOut(BaseModel):
    id: int
    attribute_id: int
    attribute_name: str
    value: str | None
    assigned: bool
    assigned_at: datetime
    changed_at: datetime


class AuditLogOut(BaseModel):
    id: int
    actor_username: str
    entity_type: str
    entity_ref: str
    action: str
    details: str | None
    created_at: datetime


class AssetOut(BaseModel):
    id: int
    assetname: str
    approved: int
    active: bool
    created_at: datetime
    changed_at: datetime
    attributes: list[AssignedAttributeOut] = Field(default_factory=list)


class AgentRegisterRequest(BaseModel):
    asset_id: int | None = None
    assetname: str | None = Field(default=None, min_length=1, max_length=255)


class AgentRegisterResponse(BaseModel):
    id: int
    assetname: str
    approved: int
    systempass: str


class AgentAuthRequest(BaseModel):
    asset_id: int
    systempass: str = Field(min_length=1, max_length=255)
    operating_system: str | None = Field(default=None, min_length=1, max_length=120)


class AgentAttributeTaskOut(BaseModel):
    attribute_name: str
    data_type: str
    allow_multiple: bool
    commands: list[str]


class AgentBootstrapResponse(BaseModel):
    approved: int
    asset_id: int
    assetname: str
    tasks: list[AgentAttributeTaskOut] = Field(default_factory=list)


class AgentAttributeValueIn(BaseModel):
    attribute_name: str = Field(min_length=1, max_length=120)
    value: str | None = None


class AgentReportRequest(BaseModel):
    asset_id: int
    systempass: str = Field(min_length=1, max_length=255)
    values: list[AgentAttributeValueIn] = Field(default_factory=list)


def _load_tuxcmdb_database_url(path: Path) -> str | None:
    """Read database.url from a tuxcmdb database.yaml config file."""
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    db_cfg = data.get("database")
    if not isinstance(db_cfg, dict):
        return None
    url = db_cfg.get("url")
    return str(url) if isinstance(url, str) and url else None


def load_api_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError("Invalid API config format")
    api_cfg = data.get("api")
    if not isinstance(api_cfg, dict):
        return {}
    return api_cfg


def normalize_assetname(value: str) -> str:
    return value.strip().lower()


def normalize_operatingsystem_name(value: str) -> str:
    return value.strip().lower()


def normalize_aliases(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        alias = str(item or "").strip()
        if not alias:
            continue
        key = alias.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(alias)
    return normalized


def aliases_to_db(values: list[str]) -> str:
    return json.dumps(normalize_aliases(values), ensure_ascii=True)


def aliases_from_db(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return normalize_aliases([str(item) for item in parsed])


def ensure_datatype_exists(conn: Connection, datatype_name: str) -> None:
    exists = conn.execute(
        select(datatypes.c.id).where(datatypes.c.name == datatype_name)
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=400, detail=f"Unknown data_type '{datatype_name}'")


def validate_by_builtin(value: str, builtin_name: str) -> bool:
    if builtin_name == "ipv4":
        try:
            ipaddress.IPv4Address(value)
            return True
        except ValueError:
            return False
    if builtin_name == "ipv6":
        try:
            ipaddress.IPv6Address(value)
            return True
        except ValueError:
            return False
    if builtin_name == "subnet":
        try:
            ipaddress.ip_network(value, strict=True)
            return True
        except ValueError:
            return False
    if builtin_name == "boolean":
        return value.strip().lower() in {"true", "false", "1", "0", "yes", "no", "on", "off"}
    if builtin_name == "integer":
        return re.fullmatch(r"^[+-]?\d+$", value.strip()) is not None
    raise HTTPException(status_code=500, detail=f"Unsupported builtin validator '{builtin_name}'")


def validate_attribute_value(conn: Connection, data_type: str, value: str | None) -> None:
    if value is None:
        return

    row = conn.execute(
        select(
            datatypes.c.name,
            datatypes.c.regex_pattern,
            datatypes.c.builtin_validator,
        ).where(datatypes.c.name == data_type)
    ).one_or_none()

    if row is None:
        raise HTTPException(status_code=400, detail=f"Unknown data_type '{data_type}'")

    if row.builtin_validator and not validate_by_builtin(value, row.builtin_validator):
        raise HTTPException(status_code=400, detail=f"Value '{value}' is not valid for data_type '{data_type}'")

    if row.regex_pattern:
        try:
            if re.fullmatch(row.regex_pattern, value) is None:
                raise HTTPException(status_code=400, detail=f"Value '{value}' is not valid for data_type '{data_type}'")
        except re.error as exc:
            raise HTTPException(status_code=500, detail=f"Invalid regex for data_type '{data_type}'") from exc


def resolve_supported_operatingsystem_ids(conn: Connection, names: list[str]) -> list[int]:
    normalized_names = [normalize_operatingsystem_name(name) for name in names if str(name).strip()]
    if not normalized_names:
        return []

    rows = conn.execute(
        select(operatingsystems.c.id, operatingsystems.c.name).where(operatingsystems.c.name.in_(normalized_names))
    ).all()
    found = {row.name: row.id for row in rows}
    missing = sorted(name for name in normalized_names if name not in found)
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown operatingsystems: {', '.join(missing)}")
    return [found[name] for name in normalized_names]


def normalize_fetchmethods(fetchmethods: list[AttributeFetchMethodIn]) -> list[AttributeFetchMethodIn]:
    normalized: list[AttributeFetchMethodIn] = []
    seen: set[str] = set()
    os_to_command: dict[str, str] = {}
    for item in fetchmethods:
        command = item.command.strip()
        if not command:
            continue
        key = command.lower()
        if key in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate fetch method command '{command}'")
        seen.add(key)

        supported_operatingsystems: list[str] = []
        seen_os_for_command: set[str] = set()
        for raw_name in item.supported_operatingsystems:
            os_name = normalize_operatingsystem_name(raw_name)
            if not os_name or os_name in seen_os_for_command:
                continue
            seen_os_for_command.add(os_name)

            existing_command = os_to_command.get(os_name)
            if existing_command and existing_command != command:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Operating system '{os_name}' is already assigned to fetch method "
                        f"'{existing_command}'. Each OS can only belong to one fetch method per attribute."
                    ),
                )
            os_to_command[os_name] = command
            supported_operatingsystems.append(os_name)

        normalized.append(
            AttributeFetchMethodIn(
                command=command,
                supported_operatingsystems=supported_operatingsystems,
            )
        )
    return normalized


def replace_attribute_fetchmethods(conn: Connection, attribute_id: int, fetchmethods: list[AttributeFetchMethodIn]) -> None:
    conn.execute(
        attribute_fetchmethods.delete().where(attribute_fetchmethods.c.attribute_id == attribute_id)
    )

    normalized_fetchmethods = normalize_fetchmethods(fetchmethods)
    for item in normalized_fetchmethods:
        supported_os_ids = resolve_supported_operatingsystem_ids(conn, item.supported_operatingsystems)
        insert_result = conn.execute(
            attribute_fetchmethods.insert().values(
                attribute_id=attribute_id,
                command=item.command,
            )
        )
        fetchmethod_id = insert_result.inserted_primary_key[0]
        conn.execute(
            attribute_fetchmethod_operatingsystems.insert(),
            [
                {"fetchmethod_id": fetchmethod_id, "operatingsystem_id": operatingsystem_id}
                for operatingsystem_id in supported_os_ids
            ],
        )


def fetch_fetchmethods_for_attributes(conn: Connection, attribute_ids: list[int]) -> dict[int, list[AttributeFetchMethodOut]]:
    if not attribute_ids:
        return {}

    rows = conn.execute(
        select(
            attribute_fetchmethods.c.attribute_id,
            attribute_fetchmethods.c.id.label("fetchmethod_id"),
            attribute_fetchmethods.c.command,
            operatingsystems.c.name,
        )
        .join(
            attribute_fetchmethod_operatingsystems,
            attribute_fetchmethod_operatingsystems.c.fetchmethod_id == attribute_fetchmethods.c.id,
        )
        .join(
            operatingsystems,
            operatingsystems.c.id == attribute_fetchmethod_operatingsystems.c.operatingsystem_id,
        )
        .where(attribute_fetchmethods.c.attribute_id.in_(attribute_ids))
        .order_by(attribute_fetchmethods.c.attribute_id, attribute_fetchmethods.c.id, operatingsystems.c.name)
    ).all()

    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        key = (row.attribute_id, row.fetchmethod_id)
        if key not in grouped:
            grouped[key] = {
                "command": row.command,
                "supported_operatingsystems": [],
            }
        grouped[key]["supported_operatingsystems"].append(row.name)

    out: dict[int, list[AttributeFetchMethodOut]] = {attribute_id: [] for attribute_id in attribute_ids}
    for (attribute_id, _fetchmethod_id), item in grouped.items():
        out.setdefault(attribute_id, []).append(AttributeFetchMethodOut(**item))

    return out


def to_attribute_out(row: Any, fetchmethods: list[AttributeFetchMethodOut] | None = None) -> AttributeOut:
    return AttributeOut(
        id=row.id,
        name=row.name,
        data_type=row.data_type,
        allow_multiple=row.allow_multiple,
        immutable=row.immutable,
        description=row.description,
        fetchmethods=fetchmethods or [],
        created_at=row.created_at,
        changed_at=row.changed_at,
    )


def to_operatingsystem_out(row: Any) -> OperatingSystemOut:
    return OperatingSystemOut(
        id=row.id,
        name=row.name,
        description=row.description,
        aliases=aliases_from_db(row.aliases),
        created_at=row.created_at,
        changed_at=row.changed_at,
    )


def latest_assignment_subquery():
    return (
        select(
            assignments.c.asset_id,
            assignments.c.attribute_id,
            func.max(assignments.c.id).label("latest_id"),
        )
        .group_by(assignments.c.asset_id, assignments.c.attribute_id)
        .subquery()
    )


def fetch_current_attributes_for_assets(conn: Connection, asset_ids: list[int]) -> dict[int, list[AssignedAttributeOut]]:
    if not asset_ids:
        return {}

    history_counts = {
        (row.asset_id, row.attribute_id): row.history_count
        for row in conn.execute(
            select(
                assignments.c.asset_id,
                assignments.c.attribute_id,
                func.count(assignments.c.id).label("history_count"),
            )
            .where(assignments.c.asset_id.in_(asset_ids))
            .group_by(assignments.c.asset_id, assignments.c.attribute_id)
        ).all()
    }

    rows = conn.execute(
        select(
            assignments.c.asset_id,
            assignments.c.attribute_id,
            assignments.c.value,
            assignments.c.assigned_at,
            attributes.c.name,
            attributes.c.allow_multiple,
            assignments.c.id,
        )
        .join(attributes, attributes.c.id == assignments.c.attribute_id)
        .where(assignments.c.asset_id.in_(asset_ids), assignments.c.assigned.is_(True))
        .order_by(assignments.c.asset_id, attributes.c.name, assignments.c.assigned_at, assignments.c.id)
    ).all()

    out: dict[int, list[AssignedAttributeOut]] = {asset_id: [] for asset_id in asset_ids}
    singleton_rows: dict[tuple[int, int], Any] = {}
    for row in rows:
        if row.allow_multiple:
            out[row.asset_id].append(
                AssignedAttributeOut(
                    attribute_id=row.attribute_id,
                    name=row.name,
                    value=row.value,
                    assigned_at=row.assigned_at,
                    history_count=history_counts.get((row.asset_id, row.attribute_id), 1),
                )
            )
            continue

        singleton_rows[(row.asset_id, row.attribute_id)] = row

    for row in singleton_rows.values():
        out[row.asset_id].append(
            AssignedAttributeOut(
                attribute_id=row.attribute_id,
                name=row.name,
                value=row.value,
                assigned_at=row.assigned_at,
                history_count=history_counts.get((row.asset_id, row.attribute_id), 1),
            )
        )

    for asset_id, attributes_list in out.items():
        attributes_list.sort(key=lambda item: (item.name, item.assigned_at, item.attribute_id))
    return out


def apply_assignment_policy(conn: Connection, asset_id: int, attribute_row: Any) -> None:
    if attribute_row.allow_multiple:
        return

    conn.execute(
        assignments.update()
        .where(
            assignments.c.asset_id == asset_id,
            assignments.c.attribute_id == attribute_row.id,
            assignments.c.assigned.is_(True),
        )
        .values(assigned=False, changed_at=func.now())
    )


def has_same_active_assignment(conn: Connection, asset_id: int, attribute_id: int, value: str | None) -> bool:
    normalized_value = normalize_assignment_value(value)
    normalized_db_value = func.replace(
        func.replace(func.coalesce(assignments.c.value, ""), "\r\n", "\n"),
        "\r",
        "\n",
    )

    if normalized_value is None:
        value_match_clause = assignments.c.value.is_(None)
    else:
        value_match_clause = normalized_db_value == normalized_value

    existing = conn.execute(
        select(assignments.c.id)
        .where(
            assignments.c.asset_id == asset_id,
            assignments.c.attribute_id == attribute_id,
            assignments.c.assigned.is_(True),
            value_match_clause,
        )
        .limit(1)
    ).scalar_one_or_none()
    return existing is not None


def log_audit_entry(
    conn: Connection,
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


def build_asset_out(rows: list[Any], conn: Connection) -> list[AssetOut]:
    asset_ids = [row.id for row in rows]
    attrs_by_asset = fetch_current_attributes_for_assets(conn, asset_ids)
    return [
        AssetOut(
            id=row.id,
            assetname=row.assetname,
            approved=row.approved,
            active=row.active,
            created_at=row.created_at,
            changed_at=row.changed_at,
            attributes=attrs_by_asset.get(row.id, []),
        )
        for row in rows
    ]


def _new_systempass() -> str:
    return secrets.token_urlsafe(24)


def verify_agent_credentials(conn: Connection, asset_id: int, systempass: str) -> Any:
    row = conn.execute(
        select(
            assets.c.id,
            assets.c.assetname,
            assets.c.active,
            assets.c.approved,
            assets.c.systempass_hash,
        ).where(assets.c.id == asset_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not row.systempass_hash or not check_password_hash(row.systempass_hash, systempass):
        raise HTTPException(status_code=403, detail="Invalid asset credentials")
    return row


def _canonical_os_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def normalize_assignment_value(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("\r\n", "\n").replace("\r", "\n")


def resolve_agent_operatingsystem_ids(conn: Connection, operating_system: str) -> list[int]:
    normalized_os = normalize_operatingsystem_name(operating_system)
    canonical_os = _canonical_os_key(normalized_os)
    if not normalized_os:
        return []

    rows = conn.execute(
        select(operatingsystems.c.id, operatingsystems.c.name, operatingsystems.c.aliases)
    ).all()

    matched_ids: list[int] = []
    for row in rows:
        keys = {
            normalize_operatingsystem_name(row.name),
            _canonical_os_key(row.name),
        }
        for alias in aliases_from_db(row.aliases):
            keys.add(normalize_operatingsystem_name(alias))
            keys.add(_canonical_os_key(alias))

        if normalized_os in keys or canonical_os in keys:
            matched_ids.append(row.id)

    return matched_ids


def fetch_agent_tasks(conn: Connection, operating_system: str) -> list[AgentAttributeTaskOut]:
    matched_os_ids = resolve_agent_operatingsystem_ids(conn, operating_system)
    if not matched_os_ids:
        return []

    rows = conn.execute(
        select(
            attributes.c.name,
            attributes.c.data_type,
            attributes.c.allow_multiple,
            attribute_fetchmethods.c.command,
        )
        .join(attribute_fetchmethods, attribute_fetchmethods.c.attribute_id == attributes.c.id)
        .join(
            attribute_fetchmethod_operatingsystems,
            attribute_fetchmethod_operatingsystems.c.fetchmethod_id == attribute_fetchmethods.c.id,
        )
        .join(
            operatingsystems,
            operatingsystems.c.id == attribute_fetchmethod_operatingsystems.c.operatingsystem_id,
        )
        .where(operatingsystems.c.id.in_(matched_os_ids))
        .order_by(attributes.c.name, attribute_fetchmethods.c.command)
    ).all()

    grouped: dict[str, AgentAttributeTaskOut] = {}
    for row in rows:
        key = row.name
        if key not in grouped:
            grouped[key] = AgentAttributeTaskOut(
                attribute_name=row.name,
                data_type=row.data_type,
                allow_multiple=row.allow_multiple,
                commands=[],
            )
        if row.command not in grouped[key].commands:
            grouped[key].commands.append(row.command)

    return list(grouped.values())


def create_app(config_path: Path = DEFAULT_API_CONFIG) -> FastAPI:
    api_cfg = load_api_config(config_path)
    database_url: str | None = api_cfg.get("database_url") or None

    if not database_url:
        database_url = os.getenv("DATABASE_URL") or None

    if not database_url:
        for candidate in TUXCMDB_DB_CONFIG_CANDIDATES:
            database_url = _load_tuxcmdb_database_url(candidate)
            if database_url:
                break

    if not database_url:
        raise ValueError(
            "No database URL configured. Set 'api.database_url' in api.yaml, "
            "set the DATABASE_URL environment variable, or run 'tuxcmdb setup' "
            "to create /opt/tuxcmdb/conf/database.yaml."
        )

    engine = create_db_engine(database_url)

    metadata.create_all(
        engine,
        tables=[
            datatypes,
            audit_log,
            operatingsystems,
            attribute_fetchmethods,
            attribute_fetchmethod_operatingsystems,
        ],
    )
    default_datatypes = [
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
        for row in default_datatypes:
            exists = conn.execute(select(datatypes.c.id).where(datatypes.c.name == row["name"])).scalar_one_or_none()
            if exists is None:
                conn.execute(datatypes.insert().values(**row))

        os_attribute_exists = conn.execute(
            select(attributes.c.id).where(attributes.c.name == "os")
        ).scalar_one_or_none()
        if os_attribute_exists is None:
            conn.execute(
                attributes.insert().values(
                    name="os",
                    data_type="string",
                    allow_multiple=False,
                    immutable=True,
                    description="Detected operating system",
                )
            )

    app = FastAPI(title="tuxcmdb-api", docs_url=None, redoc_url=None)

    def authenticate(credentials: HTTPBasicCredentials = Depends(security)) -> AuthenticatedUser:
        with engine.connect() as conn:
            row = conn.execute(
                select(
                    apiusers.c.username,
                    apiusers.c.password_hash,
                    apiusers.c.is_active,
                    apiusers.c.readonly,
                ).where(apiusers.c.username == credentials.username)
            ).one_or_none()

        is_valid_user = row is not None and row.is_active
        # Always run check_password_hash to prevent user enumeration via timing
        hash_to_check = row.password_hash if row is not None else "x" * 60
        password_ok = check_password_hash(hash_to_check, credentials.password)

        if not (is_valid_user and password_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": 'Basic realm="tuxcmdb-api"'},
            )

        return AuthenticatedUser(username=row.username, readonly=row.readonly)

    def require_write_access(user: AuthenticatedUser = Depends(authenticate)) -> AuthenticatedUser:
        if user.readonly:
            raise HTTPException(status_code=403, detail="User has readonly access")
        return user

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/ok", response_model=OkResponse)
    def ok(user: AuthenticatedUser = Depends(authenticate)) -> OkResponse:
        return OkResponse(status="ok", user=user.username, readonly=user.readonly)

    @app.get("/v1/datatypes", response_model=list[DatatypeOut])
    def list_datatypes(_: AuthenticatedUser = Depends(authenticate)) -> list[DatatypeOut]:
        with engine.connect() as conn:
            rows = conn.execute(
                select(
                    datatypes.c.id,
                    datatypes.c.name,
                    datatypes.c.description,
                    datatypes.c.regex_pattern,
                    datatypes.c.builtin_validator,
                    datatypes.c.created_at,
                    datatypes.c.changed_at,
                ).order_by(datatypes.c.name)
            ).all()
        return [DatatypeOut(**row._mapping) for row in rows]

    @app.post("/v1/datatypes", response_model=DatatypeOut, status_code=status.HTTP_201_CREATED)
    def create_datatype(payload: DatatypeCreate, _: AuthenticatedUser = Depends(require_write_access)) -> DatatypeOut:
        name = payload.name.strip().lower()
        with engine.begin() as conn:
            existing = conn.execute(
                select(datatypes.c.id).where(datatypes.c.name == name)
            ).scalar_one_or_none()
            if existing is not None:
                raise HTTPException(status_code=409, detail=f"Datatype '{name}' already exists")
            insert_result = conn.execute(
                datatypes.insert().values(
                    name=name,
                    description=payload.description or None,
                    regex_pattern=payload.regex_pattern or None,
                    builtin_validator=payload.builtin_validator or None,
                )
            )
            row = conn.execute(
                select(
                    datatypes.c.id, datatypes.c.name, datatypes.c.description,
                    datatypes.c.regex_pattern, datatypes.c.builtin_validator,
                    datatypes.c.created_at, datatypes.c.changed_at,
                ).where(datatypes.c.id == insert_result.inserted_primary_key[0])
            ).one()
            log_audit_entry(
                conn,
                _.username,
                "datatype",
                name,
                "create",
                {
                    "description": payload.description or None,
                    "regex_pattern": payload.regex_pattern or None,
                    "builtin_validator": payload.builtin_validator or None,
                },
            )
        return DatatypeOut(**row._mapping)

    @app.get("/v1/operatingsystems", response_model=list[OperatingSystemOut])
    def list_operatingsystems(
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
        _: AuthenticatedUser = Depends(authenticate),
    ) -> list[OperatingSystemOut]:
        stmt = select(
            operatingsystems.c.id,
            operatingsystems.c.name,
            operatingsystems.c.description,
            operatingsystems.c.aliases,
            operatingsystems.c.created_at,
            operatingsystems.c.changed_at,
        )
        if q:
            pattern = f"%{q.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(operatingsystems.c.name).like(pattern),
                    func.lower(func.coalesce(operatingsystems.c.description, "")).like(pattern),
                    func.lower(func.coalesce(operatingsystems.c.aliases, "")).like(pattern),
                )
            )
        stmt = stmt.order_by(operatingsystems.c.name).limit(limit).offset(offset)

        with engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [to_operatingsystem_out(row) for row in rows]

    @app.post("/v1/operatingsystems", response_model=OperatingSystemOut, status_code=status.HTTP_201_CREATED)
    def create_operatingsystem(payload: OperatingSystemCreate, _: AuthenticatedUser = Depends(require_write_access)) -> OperatingSystemOut:
        name = normalize_operatingsystem_name(payload.name)
        aliases = normalize_aliases(payload.aliases)
        with engine.begin() as conn:
            existing = conn.execute(
                select(operatingsystems.c.id).where(operatingsystems.c.name == name)
            ).scalar_one_or_none()
            if existing is not None:
                raise HTTPException(status_code=409, detail=f"Operating system '{name}' already exists")

            insert_result = conn.execute(
                operatingsystems.insert().values(
                    name=name,
                    description=payload.description or None,
                    aliases=aliases_to_db(aliases),
                )
            )
            row = conn.execute(
                select(
                    operatingsystems.c.id,
                    operatingsystems.c.name,
                    operatingsystems.c.description,
                    operatingsystems.c.aliases,
                    operatingsystems.c.created_at,
                    operatingsystems.c.changed_at,
                ).where(operatingsystems.c.id == insert_result.inserted_primary_key[0])
            ).one()
            log_audit_entry(
                conn,
                _.username,
                "operatingsystem",
                name,
                "create",
                {
                    "description": payload.description or None,
                    "aliases": aliases,
                },
            )
        return to_operatingsystem_out(row)

    @app.patch("/v1/operatingsystems/{operatingsystem_id}", response_model=OperatingSystemOut)
    def update_operatingsystem(
        operatingsystem_id: int,
        payload: OperatingSystemUpdate,
        _: AuthenticatedUser = Depends(require_write_access),
    ) -> OperatingSystemOut:
        updates: dict[str, Any] = {}
        if payload.name is not None:
            updates["name"] = normalize_operatingsystem_name(payload.name)
        if payload.description is not None:
            updates["description"] = payload.description
        if payload.aliases is not None:
            updates["aliases"] = aliases_to_db(payload.aliases)

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        updates["changed_at"] = func.now()

        with engine.begin() as conn:
            try:
                update_result = conn.execute(
                    operatingsystems.update()
                    .where(operatingsystems.c.id == operatingsystem_id)
                    .values(**updates)
                )
            except IntegrityError as exc:
                raise HTTPException(status_code=409, detail="Operating system name already exists") from exc

            if update_result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Operating system not found")

            row = conn.execute(
                select(
                    operatingsystems.c.id,
                    operatingsystems.c.name,
                    operatingsystems.c.description,
                    operatingsystems.c.aliases,
                    operatingsystems.c.created_at,
                    operatingsystems.c.changed_at,
                ).where(operatingsystems.c.id == operatingsystem_id)
            ).one()
            log_audit_entry(conn, _.username, "operatingsystem", row.name, "update", updates)
        return to_operatingsystem_out(row)

    @app.delete("/v1/operatingsystems/{operatingsystem_id}", response_model=MessageResponse)
    def delete_operatingsystem(operatingsystem_id: int, _: AuthenticatedUser = Depends(require_write_access)) -> MessageResponse:
        with engine.begin() as conn:
            row = conn.execute(
                select(operatingsystems.c.id, operatingsystems.c.name).where(operatingsystems.c.id == operatingsystem_id)
            ).one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Operating system not found")

            in_use = conn.execute(
                select(attribute_fetchmethod_operatingsystems.c.fetchmethod_id)
                .where(attribute_fetchmethod_operatingsystems.c.operatingsystem_id == operatingsystem_id)
                .limit(1)
            ).scalar_one_or_none()
            if in_use is not None:
                raise HTTPException(status_code=409, detail="Operating system is in use and cannot be deleted")

            conn.execute(operatingsystems.delete().where(operatingsystems.c.id == operatingsystem_id))
            log_audit_entry(conn, _.username, "operatingsystem", row.name, "delete")

        return MessageResponse(status="ok", message="Operating system deleted")

    @app.post("/v1/attributes", response_model=AttributeOut, status_code=status.HTTP_201_CREATED)
    def create_attribute(payload: AttributeCreate, _: AuthenticatedUser = Depends(require_write_access)) -> AttributeOut:
        name = payload.name.strip().lower()
        data_type = payload.data_type.strip().lower()
        with engine.begin() as conn:
            ensure_datatype_exists(conn, data_type)

            existing = conn.execute(
                select(attributes.c.id).where(attributes.c.name == name)
            ).scalar_one_or_none()
            if existing is not None:
                raise HTTPException(status_code=409, detail="Attribute already exists")

            insert_result = conn.execute(
                attributes.insert().values(
                    name=name,
                    data_type=data_type,
                    allow_multiple=payload.allow_multiple,
                    immutable=payload.immutable,
                    description=payload.description,
                )
            )
            attribute_id = insert_result.inserted_primary_key[0]
            replace_attribute_fetchmethods(conn, attribute_id, payload.fetchmethods)

            row = conn.execute(
                select(
                    attributes.c.id,
                    attributes.c.name,
                    attributes.c.data_type,
                    attributes.c.allow_multiple,
                    attributes.c.immutable,
                    attributes.c.description,
                    attributes.c.created_at,
                    attributes.c.changed_at,
                ).where(attributes.c.id == attribute_id)
            ).one()
            log_audit_entry(
                conn,
                _.username,
                "attribute",
                name,
                "create",
                {
                    "data_type": data_type,
                    "allow_multiple": payload.allow_multiple,
                    "immutable": payload.immutable,
                    "description": payload.description,
                    "fetchmethods": [item.model_dump() for item in payload.fetchmethods],
                },
            )

            fetchmethods_by_attribute = fetch_fetchmethods_for_attributes(conn, [attribute_id])
        return to_attribute_out(row, fetchmethods_by_attribute.get(attribute_id, []))

    @app.get("/v1/attributes", response_model=list[AttributeOut])
    def list_attributes(
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
        _: AuthenticatedUser = Depends(authenticate),
    ) -> list[AttributeOut]:
        stmt = select(
            attributes.c.id,
            attributes.c.name,
            attributes.c.data_type,
            attributes.c.allow_multiple,
            attributes.c.immutable,
            attributes.c.description,
            attributes.c.created_at,
            attributes.c.changed_at,
        )
        if q:
            pattern = f"%{q.strip().lower()}%"
            fetchmethod_exists = (
                select(attribute_fetchmethods.c.id)
                .where(
                    attribute_fetchmethods.c.attribute_id == attributes.c.id,
                    func.lower(attribute_fetchmethods.c.command).like(pattern),
                )
                .exists()
            )
            stmt = stmt.where(
                or_(
                    func.lower(attributes.c.name).like(pattern),
                    func.lower(func.coalesce(attributes.c.description, "")).like(pattern),
                    fetchmethod_exists,
                )
            )
        stmt = stmt.order_by(attributes.c.name).limit(limit).offset(offset)

        with engine.connect() as conn:
            rows = conn.execute(stmt).all()
            fetchmethods_by_attribute = fetch_fetchmethods_for_attributes(conn, [row.id for row in rows])
        return [to_attribute_out(row, fetchmethods_by_attribute.get(row.id, [])) for row in rows]

    @app.patch("/v1/attributes/{attribute_id}", response_model=AttributeOut)
    def update_attribute(attribute_id: int, payload: AttributeUpdate, _: AuthenticatedUser = Depends(require_write_access)) -> AttributeOut:
        if payload.immutable is not None:
            raise HTTPException(status_code=403, detail="immutable flag cannot be changed via API")

        updates: dict[str, Any] = {}
        if payload.name is not None:
            updates["name"] = payload.name.strip().lower()
        if payload.data_type is not None:
            updates["data_type"] = payload.data_type.strip().lower()
        if payload.description is not None:
            updates["description"] = payload.description
        if payload.allow_multiple is not None:
            updates["allow_multiple"] = payload.allow_multiple

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        updates["changed_at"] = func.now()

        with engine.begin() as conn:
            immutable_row = conn.execute(
                select(attributes.c.name, attributes.c.immutable).where(attributes.c.id == attribute_id)
            ).one_or_none()
            if immutable_row is None:
                raise HTTPException(status_code=404, detail="Attribute not found")
            if immutable_row.immutable:
                raise HTTPException(status_code=403, detail=f"Attribute '{immutable_row.name}' is immutable and cannot be changed")

            if "data_type" in updates:
                ensure_datatype_exists(conn, updates["data_type"])

            try:
                update_result = conn.execute(
                    attributes.update()
                    .where(attributes.c.id == attribute_id)
                    .values(**updates)
                )
            except IntegrityError as exc:
                raise HTTPException(status_code=409, detail="Attribute name already exists") from exc

            if update_result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Attribute not found")

            row = conn.execute(
                select(
                        attributes.c.id,
                        attributes.c.name,
                        attributes.c.data_type,
                        attributes.c.allow_multiple,
                        attributes.c.immutable,
                        attributes.c.description,
                        attributes.c.created_at,
                        attributes.c.changed_at,
                    ).where(attributes.c.id == attribute_id)
            ).one()

            if payload.fetchmethods is not None:
                replace_attribute_fetchmethods(conn, attribute_id, payload.fetchmethods)

            log_audit_entry(
                conn,
                _.username,
                "attribute",
                row.name,
                "update",
                {
                    **updates,
                    **(
                        {"fetchmethods": [item.model_dump() for item in payload.fetchmethods]}
                        if payload.fetchmethods is not None
                        else {}
                    ),
                },
            )
            fetchmethods_by_attribute = fetch_fetchmethods_for_attributes(conn, [attribute_id])
        return to_attribute_out(row, fetchmethods_by_attribute.get(attribute_id, []))

    @app.delete("/v1/attributes/{attribute_id}", response_model=MessageResponse)
    def delete_attribute(attribute_id: int, _: AuthenticatedUser = Depends(require_write_access)) -> MessageResponse:
        with engine.begin() as conn:
            row = conn.execute(
                select(attributes.c.id, attributes.c.name, attributes.c.immutable).where(attributes.c.id == attribute_id)
            ).one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Attribute not found")

            if row.immutable:
                raise HTTPException(status_code=403, detail=f"Attribute '{row.name}' is immutable and cannot be deleted")

            in_use = conn.execute(
                select(assignments.c.id).where(assignments.c.attribute_id == attribute_id).limit(1)
            ).scalar_one_or_none()
            if in_use is not None:
                raise HTTPException(status_code=409, detail="Attribute is in use and cannot be deleted")

            conn.execute(attributes.delete().where(attributes.c.id == attribute_id))
            log_audit_entry(conn, _.username, "attribute", row.name, "delete")

        return MessageResponse(status="ok", message="Attribute deleted")

    @app.post("/v1/assets", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
    def create_asset(payload: AssetCreate, _: AuthenticatedUser = Depends(require_write_access)) -> AssetOut:
        assetname = normalize_assetname(payload.assetname)
        with engine.begin() as conn:
            try:
                insert_result = conn.execute(
                    assets.insert().values(assetname=assetname, approved=APPROVAL_NOT_PENDING, systempass_hash=None, active=True)
                )
            except IntegrityError as exc:
                raise HTTPException(status_code=409, detail="Asset assetname already exists") from exc

            asset_id = insert_result.inserted_primary_key[0]
            row = conn.execute(
                select(
                    assets.c.id,
                    assets.c.assetname,
                    assets.c.approved,
                    assets.c.active,
                    assets.c.created_at,
                    assets.c.changed_at,
                ).where(assets.c.id == asset_id)
            ).one()
            log_audit_entry(conn, _.username, "asset", assetname, "create", {"active": True, "approved": APPROVAL_NOT_PENDING})

            out = build_asset_out([row], conn)[0]
        return out

    @app.get("/v1/assets", response_model=list[AssetOut])
    def list_assets(
        q: str | None = None,
        active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
        _: AuthenticatedUser = Depends(authenticate),
    ) -> list[AssetOut]:
        stmt = select(
            assets.c.id,
            assets.c.assetname,
            assets.c.approved,
            assets.c.active,
            assets.c.created_at,
            assets.c.changed_at,
        )
        if active is not None:
            stmt = stmt.where(assets.c.active.is_(active))
        if q:
            pattern = f"%{q.strip().lower()}%"
            stmt = stmt.where(func.lower(assets.c.assetname).like(pattern))

        stmt = stmt.order_by(assets.c.assetname).limit(limit).offset(offset)
        with engine.connect() as conn:
            rows = conn.execute(stmt).all()
            return build_asset_out(rows, conn)

    @app.get("/v1/assets/by-attribute", response_model=list[AssetOut])
    def list_assets_by_attribute(
        attribute_name: str | None = None,
        attribute_id: int | None = None,
        value: str | None = None,
        active: bool | None = True,
        _: AuthenticatedUser = Depends(authenticate),
    ) -> list[AssetOut]:
        if attribute_name is None and attribute_id is None:
            raise HTTPException(status_code=400, detail="Provide attribute_name or attribute_id")

        latest = latest_assignment_subquery()
        stmt = (
            select(
                assets.c.id,
                assets.c.assetname,
                assets.c.approved,
                assets.c.active,
                assets.c.created_at,
                assets.c.changed_at,
            )
            .join(latest, latest.c.asset_id == assets.c.id)
            .join(assignments, assignments.c.id == latest.c.latest_id)
            .join(attributes, attributes.c.id == assignments.c.attribute_id)
            .where(assignments.c.assigned.is_(True))
        )

        if active is not None:
            stmt = stmt.where(assets.c.active.is_(active))
        if attribute_id is not None:
            stmt = stmt.where(attributes.c.id == attribute_id)
        if attribute_name is not None:
            stmt = stmt.where(attributes.c.name == attribute_name.strip().lower())
        if value is not None:
            stmt = stmt.where(func.lower(func.coalesce(assignments.c.value, "")).like(f"%{value.strip().lower()}%"))

        stmt = stmt.distinct().order_by(assets.c.assetname)
        with engine.connect() as conn:
            rows = conn.execute(stmt).all()
            return build_asset_out(rows, conn)

    @app.get("/v1/assets/{asset_id}", response_model=AssetOut)
    def get_asset(asset_id: int, _: AuthenticatedUser = Depends(authenticate)) -> AssetOut:
        with engine.connect() as conn:
            row = conn.execute(
                select(
                    assets.c.id,
                    assets.c.assetname,
                    assets.c.approved,
                    assets.c.active,
                    assets.c.created_at,
                    assets.c.changed_at,
                ).where(assets.c.id == asset_id)
            ).one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Asset not found")
            return build_asset_out([row], conn)[0]

    @app.patch("/v1/assets/{asset_id}", response_model=AssetOut)
    def update_asset(asset_id: int, payload: AssetUpdate, _: AuthenticatedUser = Depends(require_write_access)) -> AssetOut:
        updates: dict[str, Any] = {}
        if payload.assetname is not None:
            updates["assetname"] = normalize_assetname(payload.assetname)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        updates["changed_at"] = func.now()

        with engine.begin() as conn:
            try:
                update_result = conn.execute(
                    assets.update()
                    .where(assets.c.id == asset_id)
                    .values(**updates)
                )
            except IntegrityError as exc:
                raise HTTPException(status_code=409, detail="Asset assetname already exists") from exc

            if update_result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Asset not found")

            row = conn.execute(
                select(
                    assets.c.id,
                    assets.c.assetname,
                    assets.c.approved,
                    assets.c.active,
                    assets.c.created_at,
                    assets.c.changed_at,
                ).where(assets.c.id == asset_id)
            ).one()
            log_audit_entry(conn, _.username, "asset", row.assetname, "update", updates)
            return build_asset_out([row], conn)[0]

    @app.post("/v1/assets/{asset_id}/approve", response_model=AssetOut)
    def approve_asset(asset_id: int, _: AuthenticatedUser = Depends(require_write_access)) -> AssetOut:
        with engine.begin() as conn:
            updated = conn.execute(
                assets.update()
                .where(assets.c.id == asset_id)
                .values(approved=APPROVAL_APPROVED, changed_at=func.now())
            )
            if updated.rowcount == 0:
                raise HTTPException(status_code=404, detail="Asset not found")

            row = conn.execute(
                select(
                    assets.c.id,
                    assets.c.assetname,
                    assets.c.approved,
                    assets.c.active,
                    assets.c.created_at,
                    assets.c.changed_at,
                ).where(assets.c.id == asset_id)
            ).one()
            log_audit_entry(conn, _.username, "asset", row.assetname, "approve")
            return build_asset_out([row], conn)[0]

    @app.post("/v1/assets/approve-all", response_model=MessageResponse)
    def approve_all_assets(_: AuthenticatedUser = Depends(require_write_access)) -> MessageResponse:
        with engine.begin() as conn:
            conn.execute(
                assets.update()
                .where(assets.c.approved == APPROVAL_PENDING)
                .values(approved=APPROVAL_APPROVED, changed_at=func.now())
            )
            log_audit_entry(conn, _.username, "asset", "*", "approve_all")
        return MessageResponse(status="ok", message="All pending assets approved")

    @app.post("/v1/agent/register", response_model=AgentRegisterResponse, status_code=status.HTTP_201_CREATED)
    def register_agent(payload: AgentRegisterRequest) -> AgentRegisterResponse:
        with engine.begin() as conn:
            if payload.asset_id is not None:
                row = conn.execute(
                    select(assets.c.id, assets.c.assetname, assets.c.systempass_hash).where(assets.c.id == payload.asset_id)
                ).one_or_none()
                if row is None:
                    raise HTTPException(status_code=404, detail="Asset not found")
                if row.systempass_hash:
                    raise HTTPException(status_code=403, detail="Asset already registered; agent registration denied")
                asset_id = row.id
                assetname = row.assetname
            else:
                if not payload.assetname:
                    raise HTTPException(status_code=400, detail="assetname is required when asset_id is not provided")
                assetname = normalize_assetname(payload.assetname)
                try:
                    insert_result = conn.execute(
                        assets.insert().values(
                            assetname=assetname,
                            approved=APPROVAL_PENDING,
                            systempass_hash=None,
                            active=True,
                        )
                    )
                except IntegrityError as exc:
                    raise HTTPException(status_code=409, detail="Asset assetname already exists") from exc
                asset_id = insert_result.inserted_primary_key[0]

            systempass = _new_systempass()
            conn.execute(
                assets.update()
                .where(assets.c.id == asset_id)
                .values(
                    approved=APPROVAL_PENDING,
                    systempass_hash=generate_password_hash(systempass),
                    changed_at=func.now(),
                )
            )
            log_audit_entry(conn, "agent-registration", "asset", str(asset_id), "register")
            return AgentRegisterResponse(
                id=asset_id,
                assetname=assetname,
                approved=APPROVAL_PENDING,
                systempass=systempass,
            )

    @app.post("/v1/agent/bootstrap", response_model=AgentBootstrapResponse)
    def agent_bootstrap(payload: AgentAuthRequest) -> AgentBootstrapResponse:
        with engine.connect() as conn:
            row = verify_agent_credentials(conn, payload.asset_id, payload.systempass)
            if not row.active:
                raise HTTPException(status_code=409, detail="Asset is decommissioned")

            if row.approved != APPROVAL_APPROVED:
                return AgentBootstrapResponse(
                    approved=row.approved,
                    asset_id=row.id,
                    assetname=row.assetname,
                    tasks=[],
                )

            if not payload.operating_system:
                raise HTTPException(status_code=400, detail="operating_system is required for approved assets")

            tasks = fetch_agent_tasks(conn, payload.operating_system)
            return AgentBootstrapResponse(
                approved=row.approved,
                asset_id=row.id,
                assetname=row.assetname,
                tasks=tasks,
            )

    @app.post("/v1/agent/report", response_model=MessageResponse)
    def agent_report(payload: AgentReportRequest) -> MessageResponse:
        with engine.begin() as conn:
            asset_row = verify_agent_credentials(conn, payload.asset_id, payload.systempass)
            if not asset_row.active:
                raise HTTPException(status_code=409, detail="Asset is decommissioned")
            if asset_row.approved != APPROVAL_APPROVED:
                raise HTTPException(status_code=403, detail=f"Asset is not approved (state={asset_row.approved})")

            updated_count = 0
            for item in payload.values:
                normalized_name = item.attribute_name.strip().lower()
                attribute_row = conn.execute(
                    select(attributes.c.id, attributes.c.data_type, attributes.c.allow_multiple)
                    .where(attributes.c.name == normalized_name)
                ).one_or_none()
                if attribute_row is None:
                    continue

                validate_attribute_value(conn, attribute_row.data_type, item.value)
                if has_same_active_assignment(conn, asset_row.id, attribute_row.id, item.value):
                    continue
                apply_assignment_policy(conn, asset_row.id, attribute_row)
                conn.execute(
                    assignments.insert().values(
                        asset_id=asset_row.id,
                        attribute_id=attribute_row.id,
                        value=item.value,
                        assigned=True,
                    )
                )
                updated_count += 1

            log_audit_entry(
                conn,
                f"agent:{asset_row.id}",
                "asset",
                asset_row.assetname,
                "agent_report",
                {"updated_count": updated_count},
            )
        return MessageResponse(status="ok", message=f"Assignments updated: {updated_count}")

    @app.post("/v1/assets/{asset_ref}/attributes", response_model=MessageResponse)
    def add_asset_attribute(asset_ref: str, payload: dict[str, Any], _: AuthenticatedUser = Depends(require_write_access)) -> MessageResponse:
        attribute_id, attribute_name, value = parse_asset_assign_payload(payload)

        with engine.begin() as conn:
            asset_row = resolve_asset_ref(conn, asset_ref)
            if not asset_row.active:
                raise HTTPException(status_code=409, detail="Asset is decommissioned")

            if attribute_id is not None:
                attribute_row = conn.execute(
                    select(attributes.c.id, attributes.c.data_type, attributes.c.allow_multiple).where(attributes.c.id == attribute_id)
                ).one_or_none()
            else:
                attribute_row = conn.execute(
                    select(attributes.c.id, attributes.c.data_type, attributes.c.allow_multiple).where(attributes.c.name == attribute_name)
                ).one_or_none()
            if attribute_row is None:
                raise HTTPException(status_code=404, detail="Attribute not found")

            validate_attribute_value(conn, attribute_row.data_type, value)

            if has_same_active_assignment(conn, asset_row.id, attribute_row.id, value):
                attribute_name_for_log = conn.execute(
                    select(attributes.c.name).where(attributes.c.id == attribute_row.id)
                ).scalar_one()
                log_audit_entry(
                    conn,
                    _.username,
                    "assignment",
                    f"{asset_row.id}:{attribute_name_for_log}",
                    "assign-skip-unchanged",
                    {"asset": asset_ref, "attribute": attribute_name_for_log, "value": value},
                )
                return MessageResponse(status="ok", message="Assignment unchanged")

            apply_assignment_policy(conn, asset_row.id, attribute_row)

            conn.execute(
                assignments.insert().values(
                    asset_id=asset_row.id,
                    attribute_id=attribute_row.id,
                    value=value,
                    assigned=True,
                )
            )
            attribute_name_for_log = conn.execute(
                select(attributes.c.name).where(attributes.c.id == attribute_row.id)
            ).scalar_one()
            log_audit_entry(
                conn,
                _.username,
                "assignment",
                f"{asset_row.id}:{attribute_name_for_log}",
                "assign",
                {"asset": asset_ref, "attribute": attribute_name_for_log, "value": value},
            )

        return MessageResponse(status="ok", message="Attribute assigned to asset")

    @app.delete("/v1/assets/{asset_ref}/attributes/{attribute_ref}", response_model=MessageResponse)
    def remove_asset_attribute(
        asset_ref: str,
        attribute_ref: str,
        value: str | None = None,
        _: AuthenticatedUser = Depends(require_write_access),
    ) -> MessageResponse:
        with engine.begin() as conn:
            asset_row = resolve_asset_ref(conn, asset_ref)
            if not asset_row.active:
                raise HTTPException(status_code=409, detail="Asset is decommissioned")

            attribute_row = resolve_attribute_ref(conn, attribute_ref)

            remove_stmt = assignments.update().where(
                assignments.c.asset_id == asset_row.id,
                assignments.c.attribute_id == attribute_row.id,
                assignments.c.assigned.is_(True),
            )
            if value is not None:
                normalized_value = normalize_assignment_value(value)
                normalized_db_value = func.replace(
                    func.replace(func.coalesce(assignments.c.value, ""), "\r\n", "\n"),
                    "\r",
                    "\n",
                )
                remove_stmt = remove_stmt.where(normalized_db_value == normalized_value)

            removed_rows = conn.execute(remove_stmt.values(assigned=False, changed_at=func.now())).rowcount or 0
            if removed_rows == 0:
                raise HTTPException(status_code=404, detail="Assignment not found")

            conn.execute(
                assignments.insert().values(
                    asset_id=asset_row.id,
                    attribute_id=attribute_row.id,
                    value=value,
                    assigned=False,
                )
            )
            log_audit_entry(
                conn,
                _.username,
                "assignment",
                f"{asset_row.id}:{attribute_row.name}",
                "remove",
                {"asset": asset_ref, "attribute": attribute_row.name, "value": value},
            )

        return MessageResponse(status="ok", message="Attribute removed from asset")

    @app.get("/v1/assets/{asset_ref}/attributes/{attribute_ref}/history", response_model=list[AssignmentHistoryOut])
    def asset_attribute_history(
        asset_ref: str,
        attribute_ref: str,
        _: AuthenticatedUser = Depends(authenticate),
    ) -> list[AssignmentHistoryOut]:
        with engine.connect() as conn:
            asset_row = resolve_asset_ref(conn, asset_ref)
            attribute_row = resolve_attribute_ref(conn, attribute_ref)

            rows = conn.execute(
                select(
                    assignments.c.id,
                    assignments.c.attribute_id,
                    attributes.c.name.label("attribute_name"),
                    assignments.c.value,
                    assignments.c.assigned,
                    assignments.c.assigned_at,
                    assignments.c.changed_at,
                )
                .join(attributes, attributes.c.id == assignments.c.attribute_id)
                .where(
                    assignments.c.asset_id == asset_row.id,
                    assignments.c.attribute_id == attribute_row.id,
                )
                .order_by(assignments.c.assigned_at.desc(), assignments.c.id.desc())
            ).all()

        return [AssignmentHistoryOut(**row._mapping) for row in rows]

    @app.get("/v1/audit", response_model=list[AuditLogOut])
    def list_audit(_: AuthenticatedUser = Depends(authenticate)) -> list[AuditLogOut]:
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
        return [AuditLogOut(**row._mapping) for row in rows]

    @app.post("/v1/assets/{asset_id}/decommission", response_model=AssetOut)
    def decommission_asset(asset_id: int, _: AuthenticatedUser = Depends(require_write_access)) -> AssetOut:
        with engine.begin() as conn:
            update_result = conn.execute(
                assets.update()
                .where(assets.c.id == asset_id)
                .values(active=False, changed_at=func.now())
            )
            if update_result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Asset not found")
            row = conn.execute(
                select(
                    assets.c.id,
                    assets.c.assetname,
                    assets.c.approved,
                    assets.c.active,
                    assets.c.created_at,
                    assets.c.changed_at,
                ).where(assets.c.id == asset_id)
            ).one()
            log_audit_entry(conn, _.username, "asset", row.assetname, "decommission", {"active": False})
            return build_asset_out([row], conn)[0]

    return app


def main() -> None:
    api_cfg = load_api_config(DEFAULT_API_CONFIG)
    host = str(api_cfg.get("host", "127.0.0.1"))
    port = int(api_cfg.get("port", 8080))
    app = create_app(DEFAULT_API_CONFIG)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
