"""Scope assetname uniqueness by origin in a portable way.

Revision 0018 used partial unique indexes, which MySQL/MariaDB silently
ignore: it created two *full* unique keys on assetname there, so manual and
agent assets still collided. Replace them with an explicit origin column and
one composite unique index, which every supported backend enforces the same.

Revision ID: 20260930_0019
Revises: 20260930_0018
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_0019"
down_revision = "20260930_0018"
branch_labels = None
depends_on = None

_MANUAL_INDEX = "uq_assets_manual_assetname"
_AGENT_INDEX = "uq_assets_agent_assetname"
_SCOPED_INDEX = "uq_assets_assetname_is_agent"


def _assets_table() -> sa.Table:
    return sa.table(
        "assets",
        sa.column("is_agent", sa.Boolean),
        sa.column("systempass_hash", sa.String),
    )


def _asset_index_names(bind) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_indexes("assets")}


def _asset_column_names(bind) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_columns("assets")}


def upgrade() -> None:
    bind = op.get_bind()

    for index_name in (_MANUAL_INDEX, _AGENT_INDEX):
        if index_name in _asset_index_names(bind):
            op.drop_index(index_name, table_name="assets")

    if "is_agent" not in _asset_column_names(bind):
        op.add_column(
            "assets",
            sa.Column("is_agent", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    assets = _assets_table()
    op.execute(assets.update().where(assets.c.systempass_hash.is_not(None)).values(is_agent=True))
    op.execute(assets.update().where(assets.c.systempass_hash.is_(None)).values(is_agent=False))

    if _SCOPED_INDEX not in _asset_index_names(bind):
        op.create_index(_SCOPED_INDEX, "assets", ["assetname", "is_agent"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()

    if _SCOPED_INDEX in _asset_index_names(bind):
        op.drop_index(_SCOPED_INDEX, table_name="assets")
    if "is_agent" in _asset_column_names(bind):
        op.drop_column("assets", "is_agent")

    indexes = _asset_index_names(bind)
    if _MANUAL_INDEX not in indexes:
        op.create_index(
            _MANUAL_INDEX, "assets", ["assetname"], unique=True,
            sqlite_where=sa.text("systempass_hash IS NULL"),
            postgresql_where=sa.text("systempass_hash IS NULL"),
        )
    if _AGENT_INDEX not in indexes:
        op.create_index(
            _AGENT_INDEX, "assets", ["assetname"], unique=True,
            sqlite_where=sa.text("systempass_hash IS NOT NULL"),
            postgresql_where=sa.text("systempass_hash IS NOT NULL"),
        )
