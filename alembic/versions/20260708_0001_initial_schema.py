"""initial schema

Revision ID: 20260708_0001
Revises:
Create Date: 2026-07-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260708_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("hostname = lower(hostname)", name=op.f("ck_assets_hostname_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
        sa.UniqueConstraint("hostname", name=op.f("uq_assets_hostname")),
    )

    op.create_table(
        "attributes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("data_type", sa.String(length=32), nullable=False),
        sa.Column("allow_multiple", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
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
