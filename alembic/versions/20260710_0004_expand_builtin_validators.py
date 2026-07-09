"""expand builtin datatype validators

Revision ID: 20260710_0004
Revises: 20260710_0003
Create Date: 2026-07-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260710_0004"
down_revision: Union[str, None] = "20260710_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Upgrade integer to builtin validator form.
    conn.execute(
        sa.text(
            """
            UPDATE datatypes
            SET description = :description,
                regex_pattern = NULL,
                builtin_validator = :builtin_validator
            WHERE name = 'integer'
            """
        ),
        {
            "description": "Signed integer validated by builtin parser",
            "builtin_validator": "integer",
        },
    )

    datatypes_table = sa.table(
        "datatypes",
        sa.column("name", sa.String(length=32)),
        sa.column("description", sa.Text()),
        sa.column("regex_pattern", sa.Text()),
        sa.column("builtin_validator", sa.String(length=32)),
    )

    wanted = [
        {
            "name": "ipv6",
            "description": "IPv6 address validated with Python ipaddress",
            "regex_pattern": None,
            "builtin_validator": "ipv6",
        },
        {
            "name": "subnet",
            "description": "IPv4/IPv6 subnet in CIDR notation, for example 10.0.0.0/24",
            "regex_pattern": None,
            "builtin_validator": "subnet",
        },
        {
            "name": "boolean",
            "description": "Boolean value: true/false, 1/0, yes/no, on/off",
            "regex_pattern": None,
            "builtin_validator": "boolean",
        },
    ]

    for row in wanted:
        exists = conn.execute(
            sa.text("SELECT 1 FROM datatypes WHERE name = :name"),
            {"name": row["name"]},
        ).scalar_one_or_none()
        if exists is None:
            op.bulk_insert(datatypes_table, [row])


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("DELETE FROM datatypes WHERE name IN ('ipv6', 'subnet', 'boolean')"))

    conn.execute(
        sa.text(
            """
            UPDATE datatypes
            SET description = :description,
                regex_pattern = :regex_pattern,
                builtin_validator = NULL
            WHERE name = 'integer'
            """
        ),
        {
            "description": "Signed integer",
            "regex_pattern": r"^-?\\d+$",
        },
    )
