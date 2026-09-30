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


def _assets_approved() -> sa.Table:
    return sa.table("assets", sa.column("approved", sa.Integer))


def _convert_approved_type(bind, existing_type, target_type, server_default, using: str) -> None:
    # PostgreSQL will not implicitly cast between boolean and integer, and it
    # rejects the conversion while the old default is still attached.
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("ALTER TABLE assets ALTER COLUMN approved DROP DEFAULT"))
        op.alter_column(
            "assets",
            "approved",
            existing_type=existing_type,
            type_=target_type,
            existing_nullable=False,
            postgresql_using=using,
        )
        op.alter_column(
            "assets",
            "approved",
            existing_type=target_type,
            existing_nullable=False,
            server_default=server_default,
        )
        return

    with op.batch_alter_table("assets") as batch_op:
        batch_op.alter_column(
            "approved",
            existing_type=existing_type,
            type_=target_type,
            existing_nullable=False,
            server_default=server_default,
        )


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"]: col for col in inspect(bind).get_columns("assets")}

    if "approved" not in columns:
        with op.batch_alter_table("assets") as batch_op:
            batch_op.add_column(sa.Column("approved", sa.Integer(), nullable=False, server_default=sa.text("0")))
        return

    # Convert before rewriting values: the new states do not fit a boolean column.
    approved_type = columns["approved"]["type"]
    if not isinstance(approved_type, sa.Integer):
        _convert_approved_type(
            bind, approved_type, sa.Integer(), sa.text("0"), "approved::integer"
        )

    assets = _assets_approved()
    op.execute(assets.update().values(approved=sa.case((assets.c.approved != 0, 2), else_=1)))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"]: col for col in inspect(bind).get_columns("assets")}
    if "approved" not in columns:
        return

    assets = _assets_approved()
    op.execute(assets.update().values(approved=sa.case((assets.c.approved == 2, 1), else_=0)))

    _convert_approved_type(
        bind, columns["approved"]["type"], sa.Boolean(), sa.text("false"), "approved::boolean"
    )
