"""add foreign key from attributes.data_type to datatypes.name

Revision ID: 20260710_0003
Revises: 20260710_0002
Create Date: 2026-07-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260710_0003"
down_revision: Union[str, None] = "20260710_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Normalize and sanitize values before applying FK constraint.
    op.execute("UPDATE attributes SET data_type = lower(trim(data_type))")
    op.execute(
        """
        UPDATE attributes
        SET data_type = 'string'
        WHERE data_type IS NULL
           OR data_type = ''
           OR data_type NOT IN (SELECT name FROM datatypes)
        """
    )

    with op.batch_alter_table("attributes", recreate="always") as batch_op:
        batch_op.alter_column("data_type", existing_type=sa.String(length=32), nullable=False)
        batch_op.create_foreign_key(
            op.f("fk_attributes_data_type_datatypes"),
            "datatypes",
            ["data_type"],
            ["name"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("attributes", recreate="always") as batch_op:
        batch_op.drop_constraint(op.f("fk_attributes_data_type_datatypes"), type_="foreignkey")
