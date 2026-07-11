"""squashed initial schema

Revision ID: 20260710_0005
Revises:
Create Date: 2026-07-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260710_0005"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "apiusers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("readonly", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_apiusers")),
        sa.UniqueConstraint("username", name=op.f("uq_apiusers_username")),
    )

    op.create_table(
        "datatypes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("regex_pattern", sa.Text(), nullable=True),
        sa.Column("builtin_validator", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datatypes")),
        sa.UniqueConstraint("name", name=op.f("uq_datatypes_name")),
    )

    datatypes_table = sa.table(
        "datatypes",
        sa.column("name", sa.String(length=32)),
        sa.column("description", sa.Text()),
        sa.column("regex_pattern", sa.Text()),
        sa.column("builtin_validator", sa.String(length=32)),
    )

    op.bulk_insert(
        datatypes_table,
        [
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
        ],
    )

    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assetname", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("assetname = lower(assetname)", name=op.f("ck_assets_assetname_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
        sa.UniqueConstraint("assetname", name=op.f("uq_assets_assetname")),
    )

    op.create_table(
        "attributes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("data_type", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["data_type"], ["datatypes.name"], name=op.f("fk_attributes_data_type_datatypes"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attributes")),
        sa.UniqueConstraint("name", name=op.f("uq_attributes_name")),
    )

    op.create_table(
        "assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("attribute_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("assigned", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], name=op.f("fk_assignments_asset_id_assets"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["attribute_id"],
            ["attributes.id"],
            name=op.f("fk_assignments_attribute_id_attributes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assignments")),
    )

    op.create_index("ix_assignments_assigned", "assignments", ["assigned"], unique=False)
    op.create_index(
        "ix_assignments_asset_attribute_assigned_at",
        "assignments",
        ["asset_id", "attribute_id", "assigned_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_assignments_asset_attribute_assigned_at", table_name="assignments")
    op.drop_index("ix_assignments_assigned", table_name="assignments")
    op.drop_table("assignments")
    op.drop_table("attributes")
    op.drop_table("assets")
    op.drop_table("datatypes")
    op.drop_table("apiusers")
