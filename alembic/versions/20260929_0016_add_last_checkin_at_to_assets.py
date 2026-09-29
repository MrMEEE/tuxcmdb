"""add last_checkin_at to assets

Revision ID: 20260929_0016
Revises: 20260923_0015
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260929_0016"
down_revision: Union[str, None] = "20260923_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("assets")}
    if "last_checkin_at" in columns:
        return
    op.add_column(
        "assets",
        sa.Column("last_checkin_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("assets")}
    if "last_checkin_at" not in columns:
        return
    with op.batch_alter_table("assets") as batch_op:
        batch_op.drop_column("last_checkin_at")
