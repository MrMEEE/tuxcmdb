"""Allow one manual and one agent asset to share a name.

Revision ID: 20260930_0018
Revises: 20260929_0017
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_0018"
down_revision = "20260929_0017"
branch_labels = None
depends_on = None

_LEGACY_UNIQUE = "uq_assets_assetname"
_MANUAL_INDEX = "uq_assets_manual_assetname"
_AGENT_INDEX = "uq_assets_agent_assetname"


def _asset_constraint_names(bind) -> tuple[set[str], set[str]]:
    inspector = sa.inspect(bind)
    return (
        {item["name"] for item in inspector.get_unique_constraints("assets")},
        {item["name"] for item in inspector.get_indexes("assets")},
    )


def upgrade() -> None:
    bind = op.get_bind()
    constraints, indexes = _asset_constraint_names(bind)

    if _LEGACY_UNIQUE in constraints:
        # Batch mode also recreates the SQLite table, where the old unique
        # constraint cannot be removed with ALTER TABLE alone.
        with op.batch_alter_table("assets", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}) as batch_op:
            batch_op.drop_constraint(_LEGACY_UNIQUE, type_="unique")
    elif _LEGACY_UNIQUE in indexes:
        op.drop_index(_LEGACY_UNIQUE, table_name="assets")

    _, indexes = _asset_constraint_names(bind)
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


def downgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(sa.text(
        "SELECT assetname FROM assets GROUP BY assetname HAVING COUNT(*) > 1 LIMIT 1"
    )).scalar_one_or_none()
    if duplicate is not None:
        raise RuntimeError(f"Cannot restore global assetname uniqueness while '{duplicate}' has multiple assets")

    _, indexes = _asset_constraint_names(bind)
    if _AGENT_INDEX in indexes:
        op.drop_index(_AGENT_INDEX, table_name="assets")
    if _MANUAL_INDEX in indexes:
        op.drop_index(_MANUAL_INDEX, table_name="assets")
    with op.batch_alter_table("assets") as batch_op:
        batch_op.create_unique_constraint(_LEGACY_UNIQUE, ["assetname"])
