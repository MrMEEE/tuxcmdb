"""add name and description to apiusers

Revision ID: 20260712_0007
Revises: 20260711_0006
Create Date: 2026-07-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "20260712_0007"
down_revision: Union[str, None] = "20260711_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("apiusers")}

    with op.batch_alter_table("apiusers", recreate="always") as batch_op:
        if "name" not in columns:
            batch_op.add_column(sa.Column("name", sa.String(length=120), nullable=True))
        if "description" not in columns:
            batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))

    op.execute(sa.text("UPDATE apiusers SET name = username WHERE name IS NULL OR name = ''"))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("apiusers")}

    with op.batch_alter_table("apiusers", recreate="always") as batch_op:
        if "description" in columns:
            batch_op.drop_column("description")
        if "name" in columns:
            batch_op.drop_column("name")
