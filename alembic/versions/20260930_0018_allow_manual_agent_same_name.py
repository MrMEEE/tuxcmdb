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


def upgrade() -> None:
    # Batch mode also recreates the SQLite table, where the old unique
    # constraint cannot be removed with ALTER TABLE alone.
    with op.batch_alter_table("assets", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}) as batch_op:
        batch_op.drop_constraint("uq_assets_assetname", type_="unique")
    op.create_index(
        "uq_assets_manual_assetname", "assets", ["assetname"], unique=True,
        sqlite_where=sa.text("systempass_hash IS NULL"),
        postgresql_where=sa.text("systempass_hash IS NULL"),
    )
    op.create_index(
        "uq_assets_agent_assetname", "assets", ["assetname"], unique=True,
        sqlite_where=sa.text("systempass_hash IS NOT NULL"),
        postgresql_where=sa.text("systempass_hash IS NOT NULL"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    duplicate = conn.execute(sa.text(
        "SELECT assetname FROM assets GROUP BY assetname HAVING COUNT(*) > 1 LIMIT 1"
    )).scalar_one_or_none()
    if duplicate is not None:
        raise RuntimeError(f"Cannot restore global assetname uniqueness while '{duplicate}' has multiple assets")
    op.drop_index("uq_assets_agent_assetname", table_name="assets")
    op.drop_index("uq_assets_manual_assetname", table_name="assets")
    with op.batch_alter_table("assets") as batch_op:
        batch_op.create_unique_constraint("uq_assets_assetname", ["assetname"])
