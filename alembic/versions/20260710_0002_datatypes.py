"""add datatypes table

Revision ID: 20260710_0002
Revises: 20260708_0001
Create Date: 2026-07-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260710_0002"
down_revision: Union[str, None] = "20260708_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "datatypes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("regex_pattern", sa.Text(), nullable=True),
        sa.Column("builtin_validator", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datatypes")),
        sa.UniqueConstraint("name", name=op.f("uq_datatypes_name")),
    )

    datatypes_table = sa.table(
        "datatypes",
        sa.column("name", sa.String(length=32)),
        sa.column("description", sa.Text()),
        sa.column("regex_pattern", sa.Text()),
        sa.column("builtin_validator", sa.String(length=32)),
    )

    op.bulk_insert(
        datatypes_table,
        [
            {
                "name": "string",
                "description": "Any string (no validation)",
                "regex_pattern": None,
                "builtin_validator": None,
            },
            {
                "name": "integer",
                "description": "Signed integer",
                "regex_pattern": r"^-?\\d+$",
                "builtin_validator": None,
            },
            {
                "name": "numeric",
                "description": "Signed integer or decimal number",
                "regex_pattern": r"^-?\\d+(?:\\.\\d+)?$",
                "builtin_validator": None,
            },
            {
                "name": "ipv4",
                "description": "IPv4 address validated with Python ipaddress",
                "regex_pattern": None,
                "builtin_validator": "ipv4",
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("datatypes")
