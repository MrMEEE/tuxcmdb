"""convert assets.approved to state integer

Revision ID: 20260714_0011
Revises: 20260714_0010
Create Date: 2026-07-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "20260714_0011"
down_revision: Union[str, None] = "20260714_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"]: col for col in inspect(bind).get_columns("assets")}

    if "approved" not in columns:
        with op.batch_alter_table("assets") as batch_op:
            batch_op.add_column(sa.Column("approved", sa.Integer(), nullable=False, server_default=sa.text("0")))
        return

    op.execute(
        sa.text(
            "UPDATE assets "
            "SET approved = CASE "
            "WHEN approved IN (1, '1', true, 't', 'true') THEN 2 "
            "ELSE 1 END"
        )
    )

    approved_type = columns["approved"]["type"]
    if not isinstance(approved_type, sa.Integer):
        with op.batch_alter_table("assets") as batch_op:
            batch_op.alter_column(
                "approved",
                existing_type=approved_type,
                type_=sa.Integer(),
                existing_nullable=False,
                server_default=sa.text("0"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"]: col for col in inspect(bind).get_columns("assets")}
    if "approved" not in columns:
        return

    op.execute(
        sa.text(
            "UPDATE assets "
            "SET approved = CASE "
            "WHEN approved = 2 THEN 1 "
            "ELSE 0 END"
        )
    )

    with op.batch_alter_table("assets") as batch_op:
        batch_op.alter_column(
            "approved",
            existing_type=columns["approved"]["type"],
            type_=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.text("false"),
        )
