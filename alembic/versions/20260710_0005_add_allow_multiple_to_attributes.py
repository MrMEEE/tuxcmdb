"""add allow_multiple to attributes

Revision ID: 20260710_0005
Revises: 20260710_0004
Create Date: 2026-07-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260710_0005"
down_revision: Union[str, None] = "20260710_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("attributes", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("allow_multiple", sa.Boolean(), server_default=sa.text("false"), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("attributes", recreate="always") as batch_op:
        batch_op.drop_column("allow_multiple")
