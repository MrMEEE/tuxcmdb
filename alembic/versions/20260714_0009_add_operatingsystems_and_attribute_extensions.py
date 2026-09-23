"""add operatingsystems and attribute extensions

Revision ID: 20260714_0009
Revises: 20260712_0008
Create Date: 2026-07-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "20260714_0009"
down_revision: Union[str, None] = "20260712_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    if "operatingsystems" not in table_names:
        op.create_table(
            "operatingsystems",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("aliases", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_operatingsystems")),
            sa.UniqueConstraint("name", name=op.f("uq_operatingsystems_name")),
        )

    if "attribute_fetchmethods" not in table_names:
        op.create_table(
            "attribute_fetchmethods",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("attribute_id", sa.Integer(), nullable=False),
            sa.Column("command", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(
                ["attribute_id"],
                ["attributes.id"],
                name=op.f("fk_attribute_fetchmethods_attribute_id_attributes"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_attribute_fetchmethods")),
        )

    if "attribute_fetchmethod_operatingsystems" not in table_names:
        op.create_table(
            "attribute_fetchmethod_operatingsystems",
            sa.Column("fetchmethod_id", sa.Integer(), nullable=False),
            sa.Column("operatingsystem_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(
                ["fetchmethod_id"],
                ["attribute_fetchmethods.id"],
                name=op.f("fk_attribute_fetchmethod_operatingsystems_fetchmethod_id_attribute_fetchmethods"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["operatingsystem_id"],
                ["operatingsystems.id"],
                name=op.f("fk_attribute_fetchmethod_operatingsystems_operatingsystem_id_operatingsystems"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("fetchmethod_id", "operatingsystem_id", name=op.f("pk_attribute_fetchmethod_operatingsystems")),
        )

    asset_columns = {column["name"] for column in inspect(bind).get_columns("assets")}
    asset_foreign_keys = {
        tuple(foreign_key["constrained_columns"])
        for foreign_key in inspect(bind).get_foreign_keys("assets")
    }
    with op.batch_alter_table("assets") as batch_op:
        if "operatingsystem_id" not in asset_columns:
            batch_op.add_column(sa.Column("operatingsystem_id", sa.Integer(), nullable=True))
        if ("operatingsystem_id",) not in asset_foreign_keys:
            batch_op.create_foreign_key(
                op.f("fk_assets_operatingsystem_id_operatingsystems"),
                "operatingsystems",
                ["operatingsystem_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    with op.batch_alter_table("assets") as batch_op:
        batch_op.drop_constraint(op.f("fk_assets_operatingsystem_id_operatingsystems"), type_="foreignkey")
        batch_op.drop_column("operatingsystem_id")

    op.drop_table("attribute_fetchmethod_operatingsystems")
    op.drop_table("attribute_fetchmethods")

    op.drop_table("operatingsystems")
