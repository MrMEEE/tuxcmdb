"""add inventory_group to attributes

Revision ID: 20260923_0015
Revises: 20260723_0014, 20260923_0006
Create Date: 2026-09-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260923_0015"
down_revision: Union[str, tuple[str, str], None] = ("20260723_0014", "20260923_0006")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("attributes")}
    if "inventory_group" in columns:
        return
    op.add_column(
        "attributes",
        sa.Column(
            "inventory_group",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("attributes")}
    if "inventory_group" not in columns:
        return
    with op.batch_alter_table("attributes") as batch_op:
        batch_op.drop_column("inventory_group")