"""add readonly to apiusers

Revision ID: 20260711_0006
Revises: 20260710_0005
Create Date: 2026-07-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260711_0006"
down_revision: Union[str, None] = "20260710_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("apiusers", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("readonly", sa.Boolean(), server_default=sa.text("false"), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("apiusers", recreate="always") as batch_op:
        batch_op.drop_column("readonly")
