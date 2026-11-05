"""add allowed_values to datatypes

Revision ID: 20261013_0020
Revises: 20260930_0019
Create Date: 2026-10-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261013_0020"
down_revision: Union[str, None] = "20260930_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("datatypes")}
    if "allowed_values" not in columns:
        op.add_column("datatypes", sa.Column("allowed_values", sa.Text(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("datatypes")}
    if "allowed_values" in columns:
        with op.batch_alter_table("datatypes") as batch_op:
            batch_op.drop_column("allowed_values")
