"""add asset approval and systempass hash

Revision ID: 20260714_0010
Revises: 20260714_0009
Create Date: 2026-07-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "20260714_0010"
down_revision: Union[str, None] = "20260714_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("assets")}

    with op.batch_alter_table("assets") as batch_op:
        if "approved" not in columns:
            batch_op.add_column(sa.Column("approved", sa.Integer(), nullable=False, server_default=sa.text("0")))
        if "systempass_hash" not in columns:
            batch_op.add_column(sa.Column("systempass_hash", sa.String(length=255), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("assets")}

    with op.batch_alter_table("assets") as batch_op:
        if "systempass_hash" in columns:
            batch_op.drop_column("systempass_hash")
        if "approved" in columns:
            batch_op.drop_column("approved")
