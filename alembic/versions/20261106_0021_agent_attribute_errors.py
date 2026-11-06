"""Persist agent attribute validation status separately from values."""

from alembic import op
import sqlalchemy as sa

revision = "20261106_0021"
down_revision = "20261013_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "agent_attribute_errors" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "agent_attribute_errors",
        sa.Column("asset_id", sa.Integer(), primary_key=True),
        sa.Column("attribute_id", sa.Integer(), primary_key=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("agent_attribute_errors")
