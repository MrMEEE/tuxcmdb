"""add immutable to attributes and seed os attribute

Revision ID: 20260714_0012
Revises: 20260714_0011
Create Date: 2026-07-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "20260714_0012"
down_revision: Union[str, None] = "20260714_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("attributes")}

    with op.batch_alter_table("attributes") as batch_op:
        if "immutable" not in columns:
            batch_op.add_column(sa.Column("immutable", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    op.execute(
        sa.text(
            "INSERT INTO attributes (name, description, data_type, allow_multiple, immutable) "
            "SELECT 'os', 'Detected operating system', 'string', false, true "
            "WHERE NOT EXISTS (SELECT 1 FROM attributes WHERE name = 'os')"
        )
    )

    op.execute(sa.text("UPDATE attributes SET immutable = true WHERE name = 'os'"))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("attributes")}

    op.execute(sa.text("UPDATE attributes SET immutable = false WHERE name = 'os'"))

    with op.batch_alter_table("attributes") as batch_op:
        if "immutable" in columns:
            batch_op.drop_column("immutable")
