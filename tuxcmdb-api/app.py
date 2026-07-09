#!/usr/bin/env python3
"""Authenticated FastAPI service for TuxCMDB."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    create_engine,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash
import uvicorn
import yaml


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_API_CONFIG = BASE_DIR / "conf" / "api.yaml"

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

assets = Table(
    "assets",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("hostname", String(255), nullable=False, unique=True),
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
    Column("data_type", String(32), nullable=False),
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

security = HTTPBasic()


class HealthResponse(BaseModel):
    status: str


class OkResponse(BaseModel):
    status: str
    user: str


class MessageResponse(BaseModel):
    status: str
    message: str


class AttributeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    data_type: str = Field(default="string", min_length=1, max_length=32)
    description: str | None = None


class AttributeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    data_type: str | None = Field(default=None, min_length=1, max_length=32)
    description: str | None = None


class AttributeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    data_type: str
    description: str | None
    created_at: datetime
    changed_at: datetime


class AssetCreate(BaseModel):
    hostname: str = Field(min_length=1, max_length=255)


class AssetUpdate(BaseModel):
    hostname: str | None = Field(default=None, min_length=1, max_length=255)


class AssetAssignRequest(BaseModel):
    attribute_id: int
    value: str | None = None


class AssignedAttributeOut(BaseModel):
    attribute_id: int
    name: str
    value: str | None
    assigned_at: datetime


class AssetOut(BaseModel):
    id: int
    hostname: str
    active: bool
    created_at: datetime
    changed_at: datetime
    attributes: list[AssignedAttributeOut] = Field(default_factory=list)


def load_api_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError("Invalid API config format")
    api_cfg = data.get("api")
    if not isinstance(api_cfg, dict):
        raise ValueError("Invalid API config: missing 'api' mapping")
    return api_cfg


def normalize_hostname(value: str) -> str:
    return value.strip().lower()


def to_attribute_out(row: Any) -> AttributeOut:
    return AttributeOut(
        id=row.id,
        name=row.name,
        data_type=row.data_type,
        description=row.description,
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

    latest = latest_assignment_subquery()
    rows = conn.execute(
        select(
            assignments.c.asset_id,
            assignments.c.attribute_id,
            assignments.c.value,
            assignments.c.assigned_at,
            attributes.c.name,
        )
        .join(latest, assignments.c.id == latest.c.latest_id)
        .join(attributes, attributes.c.id == assignments.c.attribute_id)
        .where(assignments.c.asset_id.in_(asset_ids), assignments.c.assigned.is_(True))
        .order_by(assignments.c.asset_id, attributes.c.name)
    ).all()

    out: dict[int, list[AssignedAttributeOut]] = {asset_id: [] for asset_id in asset_ids}
    for row in rows:
        out[row.asset_id].append(
            AssignedAttributeOut(
                attribute_id=row.attribute_id,
                name=row.name,
                value=row.value,
                assigned_at=row.assigned_at,
            )
        )
    return out


def build_asset_out(rows: list[Any], conn: Connection) -> list[AssetOut]:
    asset_ids = [row.id for row in rows]
    attrs_by_asset = fetch_current_attributes_for_assets(conn, asset_ids)
    return [
        AssetOut(
            id=row.id,
            hostname=row.hostname,
            active=row.active,
            created_at=row.created_at,
            changed_at=row.changed_at,
            attributes=attrs_by_asset.get(row.id, []),
        )
        for row in rows
    ]


def create_app(config_path: Path = DEFAULT_API_CONFIG) -> FastAPI:
    api_cfg = load_api_config(config_path)
    database_url = api_cfg.get("database_url")
    if not isinstance(database_url, str) or not database_url:
        raise ValueError("Invalid API config: missing api.database_url")

    engine = create_engine(database_url, future=True)

    app = FastAPI(title="tuxcmdb-api", docs_url=None, redoc_url=None)

    def authenticate(credentials: HTTPBasicCredentials = Depends(security)) -> str:
        with engine.connect() as conn:
            row = conn.execute(
                select(
                    apiusers.c.username,
                    apiusers.c.password_hash,
                    apiusers.c.is_active,
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

        return row.username

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/ok", response_model=OkResponse)
    def ok(username: str = Depends(authenticate)) -> OkResponse:
        return OkResponse(status="ok", user=username)

    @app.post("/v1/attributes", response_model=AttributeOut, status_code=status.HTTP_201_CREATED)
    def create_attribute(payload: AttributeCreate, _: str = Depends(authenticate)) -> AttributeOut:
        name = payload.name.strip().lower()
        with engine.begin() as conn:
            existing = conn.execute(
                select(attributes.c.id).where(attributes.c.name == name)
            ).scalar_one_or_none()
            if existing is not None:
                raise HTTPException(status_code=409, detail="Attribute already exists")

            insert_result = conn.execute(
                attributes.insert().values(
                    name=name,
                    data_type=payload.data_type.strip().lower(),
                    description=payload.description,
                )
            )
            attribute_id = insert_result.inserted_primary_key[0]
            row = conn.execute(
                select(
                    attributes.c.id,
                    attributes.c.name,
                    attributes.c.data_type,
                    attributes.c.description,
                    attributes.c.created_at,
                    attributes.c.changed_at,
                ).where(attributes.c.id == attribute_id)
            ).one()

        return to_attribute_out(row)

    @app.get("/v1/attributes", response_model=list[AttributeOut])
    def list_attributes(
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
        _: str = Depends(authenticate),
    ) -> list[AttributeOut]:
        stmt = select(
            attributes.c.id,
            attributes.c.name,
            attributes.c.data_type,
            attributes.c.description,
            attributes.c.created_at,
            attributes.c.changed_at,
        )
        if q:
            pattern = f"%{q.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(attributes.c.name).like(pattern),
                    func.lower(func.coalesce(attributes.c.description, "")).like(pattern),
                )
            )
        stmt = stmt.order_by(attributes.c.name).limit(limit).offset(offset)

        with engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [to_attribute_out(row) for row in rows]

    @app.patch("/v1/attributes/{attribute_id}", response_model=AttributeOut)
    def update_attribute(attribute_id: int, payload: AttributeUpdate, _: str = Depends(authenticate)) -> AttributeOut:
        updates: dict[str, Any] = {}
        if payload.name is not None:
            updates["name"] = payload.name.strip().lower()
        if payload.data_type is not None:
            updates["data_type"] = payload.data_type.strip().lower()
        if payload.description is not None:
            updates["description"] = payload.description

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        updates["changed_at"] = func.now()

        with engine.begin() as conn:
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
                        attributes.c.description,
                        attributes.c.created_at,
                        attributes.c.changed_at,
                    ).where(attributes.c.id == attribute_id)
            ).one()
        return to_attribute_out(row)

    @app.delete("/v1/attributes/{attribute_id}", response_model=MessageResponse)
    def delete_attribute(attribute_id: int, _: str = Depends(authenticate)) -> MessageResponse:
        with engine.begin() as conn:
            exists = conn.execute(
                select(attributes.c.id).where(attributes.c.id == attribute_id)
            ).scalar_one_or_none()
            if exists is None:
                raise HTTPException(status_code=404, detail="Attribute not found")

            in_use = conn.execute(
                select(assignments.c.id).where(assignments.c.attribute_id == attribute_id).limit(1)
            ).scalar_one_or_none()
            if in_use is not None:
                raise HTTPException(status_code=409, detail="Attribute is in use and cannot be deleted")

            conn.execute(attributes.delete().where(attributes.c.id == attribute_id))

        return MessageResponse(status="ok", message="Attribute deleted")

    @app.post("/v1/assets", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
    def create_asset(payload: AssetCreate, _: str = Depends(authenticate)) -> AssetOut:
        hostname = normalize_hostname(payload.hostname)
        with engine.begin() as conn:
            try:
                insert_result = conn.execute(
                    assets.insert().values(hostname=hostname, active=True)
                )
            except IntegrityError as exc:
                raise HTTPException(status_code=409, detail="Asset hostname already exists") from exc

            asset_id = insert_result.inserted_primary_key[0]
            row = conn.execute(
                select(
                    assets.c.id,
                    assets.c.hostname,
                    assets.c.active,
                    assets.c.created_at,
                    assets.c.changed_at,
                ).where(assets.c.id == asset_id)
            ).one()

            out = build_asset_out([row], conn)[0]
        return out

    @app.get("/v1/assets", response_model=list[AssetOut])
    def list_assets(
        q: str | None = None,
        active: bool | None = True,
        limit: int = 100,
        offset: int = 0,
        _: str = Depends(authenticate),
    ) -> list[AssetOut]:
        stmt = select(
            assets.c.id,
            assets.c.hostname,
            assets.c.active,
            assets.c.created_at,
            assets.c.changed_at,
        )
        if active is not None:
            stmt = stmt.where(assets.c.active.is_(active))
        if q:
            pattern = f"%{q.strip().lower()}%"
            stmt = stmt.where(func.lower(assets.c.hostname).like(pattern))

        stmt = stmt.order_by(assets.c.hostname).limit(limit).offset(offset)
        with engine.connect() as conn:
            rows = conn.execute(stmt).all()
            return build_asset_out(rows, conn)

    @app.get("/v1/assets/by-attribute", response_model=list[AssetOut])
    def list_assets_by_attribute(
        attribute_name: str | None = None,
        attribute_id: int | None = None,
        value: str | None = None,
        active: bool | None = True,
        _: str = Depends(authenticate),
    ) -> list[AssetOut]:
        if attribute_name is None and attribute_id is None:
            raise HTTPException(status_code=400, detail="Provide attribute_name or attribute_id")

        latest = latest_assignment_subquery()
        stmt = (
            select(
                assets.c.id,
                assets.c.hostname,
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

        stmt = stmt.distinct().order_by(assets.c.hostname)
        with engine.connect() as conn:
            rows = conn.execute(stmt).all()
            return build_asset_out(rows, conn)

    @app.get("/v1/assets/{asset_id}", response_model=AssetOut)
    def get_asset(asset_id: int, _: str = Depends(authenticate)) -> AssetOut:
        with engine.connect() as conn:
            row = conn.execute(
                select(
                    assets.c.id,
                    assets.c.hostname,
                    assets.c.active,
                    assets.c.created_at,
                    assets.c.changed_at,
                ).where(assets.c.id == asset_id)
            ).one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Asset not found")
            return build_asset_out([row], conn)[0]

    @app.patch("/v1/assets/{asset_id}", response_model=AssetOut)
    def update_asset(asset_id: int, payload: AssetUpdate, _: str = Depends(authenticate)) -> AssetOut:
        updates: dict[str, Any] = {}
        if payload.hostname is not None:
            updates["hostname"] = normalize_hostname(payload.hostname)
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
                raise HTTPException(status_code=409, detail="Asset hostname already exists") from exc

            if update_result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Asset not found")

            row = conn.execute(
                select(
                    assets.c.id,
                    assets.c.hostname,
                    assets.c.active,
                    assets.c.created_at,
                    assets.c.changed_at,
                ).where(assets.c.id == asset_id)
            ).one()
            return build_asset_out([row], conn)[0]

    @app.post("/v1/assets/{asset_id}/attributes", response_model=MessageResponse)
    def add_asset_attribute(asset_id: int, payload: AssetAssignRequest, _: str = Depends(authenticate)) -> MessageResponse:
        with engine.begin() as conn:
            asset_row = conn.execute(
                select(assets.c.id, assets.c.active).where(assets.c.id == asset_id)
            ).one_or_none()
            if asset_row is None:
                raise HTTPException(status_code=404, detail="Asset not found")
            if not asset_row.active:
                raise HTTPException(status_code=409, detail="Asset is decommissioned")

            attribute_exists = conn.execute(
                select(attributes.c.id).where(attributes.c.id == payload.attribute_id)
            ).scalar_one_or_none()
            if attribute_exists is None:
                raise HTTPException(status_code=404, detail="Attribute not found")

            conn.execute(
                assignments.insert().values(
                    asset_id=asset_id,
                    attribute_id=payload.attribute_id,
                    value=payload.value,
                    assigned=True,
                )
            )

        return MessageResponse(status="ok", message="Attribute assigned to asset")

    @app.delete("/v1/assets/{asset_id}/attributes/{attribute_id}", response_model=MessageResponse)
    def remove_asset_attribute(asset_id: int, attribute_id: int, _: str = Depends(authenticate)) -> MessageResponse:
        with engine.begin() as conn:
            asset_row = conn.execute(
                select(assets.c.id, assets.c.active).where(assets.c.id == asset_id)
            ).one_or_none()
            if asset_row is None:
                raise HTTPException(status_code=404, detail="Asset not found")
            if not asset_row.active:
                raise HTTPException(status_code=409, detail="Asset is decommissioned")

            attribute_exists = conn.execute(
                select(attributes.c.id).where(attributes.c.id == attribute_id)
            ).scalar_one_or_none()
            if attribute_exists is None:
                raise HTTPException(status_code=404, detail="Attribute not found")

            conn.execute(
                assignments.insert().values(
                    asset_id=asset_id,
                    attribute_id=attribute_id,
                    value=None,
                    assigned=False,
                )
            )

        return MessageResponse(status="ok", message="Attribute removed from asset")

    @app.post("/v1/assets/{asset_id}/decommission", response_model=AssetOut)
    def decommission_asset(asset_id: int, _: str = Depends(authenticate)) -> AssetOut:
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
                    assets.c.hostname,
                    assets.c.active,
                    assets.c.created_at,
                    assets.c.changed_at,
                ).where(assets.c.id == asset_id)
            ).one()
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
