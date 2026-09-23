"""add is_default flag to attribute fetch methods

Revision ID: 20260722_0013
Revises: 20260714_0012
Create Date: 2026-07-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "20260722_0013"
down_revision: Union[str, None] = "20260714_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("attribute_fetchmethods")}

    with op.batch_alter_table("attribute_fetchmethods") as batch_op:
        if "is_default" not in columns:
            batch_op.add_column(sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("attribute_fetchmethods")}

    with op.batch_alter_table("attribute_fetchmethods") as batch_op:
        if "is_default" in columns:
            batch_op.drop_column("is_default")
