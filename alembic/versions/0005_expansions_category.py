"""Tabla expansions y category_name en cards.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cards", sa.Column("category_name", sa.String(length=128), nullable=True))
    op.create_table(
        "expansions",
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("id_expansion", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="nonsingles"),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("game_id", "id_expansion"),
    )


def downgrade() -> None:
    op.drop_table("expansions")
    op.drop_column("cards", "category_name")
