"""add is_default flag to attributes for automatic default attribute sync

Revision ID: 20260929_0017
Revises: 20260929_0016
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260929_0017"
down_revision: Union[str, None] = "20260929_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Attributes previously seeded by app code without a way to mark them as
# managed defaults. Backfilled here so the runtime sync in
# tuxcmdb/default_attributes.py can recognize and manage them going forward.
_PREVIOUSLY_SEEDED_DEFAULTS = ("ip_address", "vmware_uuid", "environment", "cpus", "memory_gb", "os")


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("attributes")}
    if "is_default" not in columns:
        op.add_column(
            "attributes",
            sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        )

    attributes_table = sa.table("attributes", sa.column("name", sa.String), sa.column("is_default", sa.Boolean))
    op.execute(
        attributes_table.update()
        .where(attributes_table.c.name.in_(_PREVIOUSLY_SEEDED_DEFAULTS))
        .values(is_default=True)
    )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("attributes")}
    if "is_default" not in columns:
        return
    with op.batch_alter_table("attributes") as batch_op:
        batch_op.drop_column("is_default")
