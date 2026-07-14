from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    func,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from tuxcmdb.db import Base


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint("assetname = lower(assetname)", name="ck_assets_assetname_lowercase"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assetname: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    operatingsystem_id: Mapped[int | None] = mapped_column(
        ForeignKey("operatingsystems.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    systempass_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true(), default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    assignments: Mapped[list["Assignment"]] = relationship(back_populates="asset")
    operatingsystem: Mapped["OperatingSystem | None"] = relationship()

    @validates("assetname")
    def normalize_assetname(self, key: str, value: str) -> str:
        del key
        return value.strip().lower()


class Attribute(Base):
    __tablename__ = "attributes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_type: Mapped[str] = mapped_column(String(32), ForeignKey("datatypes.name"), nullable=False, default="string")
    allow_multiple: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false(), default=False)
    immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false(), default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    assignments: Mapped[list["Assignment"]] = relationship(back_populates="attribute")
    datatype: Mapped["Datatype"] = relationship(back_populates="attributes")
    fetchmethods: Mapped[list["AttributeFetchMethod"]] = relationship(back_populates="attribute")


attribute_fetchmethod_operatingsystems = Table(
    "attribute_fetchmethod_operatingsystems",
    Base.metadata,
    Column("fetchmethod_id", Integer, ForeignKey("attribute_fetchmethods.id", ondelete="CASCADE"), primary_key=True),
    Column("operatingsystem_id", Integer, ForeignKey("operatingsystems.id", ondelete="CASCADE"), primary_key=True),
)


class AttributeFetchMethod(Base):
    __tablename__ = "attribute_fetchmethods"

    id: Mapped[int] = mapped_column(primary_key=True)
    attribute_id: Mapped[int] = mapped_column(ForeignKey("attributes.id", ondelete="CASCADE"), nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)

    attribute: Mapped[Attribute] = relationship(back_populates="fetchmethods")
    supported_operatingsystems: Mapped[list["OperatingSystem"]] = relationship(
        secondary="attribute_fetchmethod_operatingsystems",
        back_populates="fetchmethods",
    )


class OperatingSystem(Base):
    __tablename__ = "operatingsystems"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    aliases: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    fetchmethods: Mapped[list[AttributeFetchMethod]] = relationship(
        secondary="attribute_fetchmethod_operatingsystems",
        back_populates="supported_operatingsystems",
    )


class Datatype(Base):
    __tablename__ = "datatypes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    regex_pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    builtin_validator: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    attributes: Mapped[list[Attribute]] = relationship(back_populates="datatype")


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        Index("ix_assignments_assigned", "assigned"),
        Index("ix_assignments_asset_attribute_assigned_at", "asset_id", "attribute_id", "assigned_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    attribute_id: Mapped[int] = mapped_column(
        ForeignKey("attributes.id", ondelete="CASCADE"), nullable=False
    )
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true(), default=True)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    asset: Mapped[Asset] = relationship(back_populates="assignments")
    attribute: Mapped[Attribute] = relationship(back_populates="assignments")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_username: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
