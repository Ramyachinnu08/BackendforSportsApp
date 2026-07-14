"""media blobs table for db storage provider

Revision ID: a1b2c3d4e5f6
Revises: d4e374bf132a
Create Date: 2026-07-15
"""
import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "d4e374bf132a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_blobs",
        sa.Column("storage_key", sa.Text(), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=True),
        sa.Column("acl", sa.String(length=16), nullable=False, server_default="public"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("media_blobs")
