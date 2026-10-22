"""Tabla card_images para las imagenes de las cartas.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "card_images",
        sa.Column("id_product", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_url", sa.String(length=512), nullable=True),
        sa.Column("local_path", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=32), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("downloaded_at", sa.DateTime(), nullable=True),
        sa.Column("failed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id_product"),
    )
    op.create_index("ix_card_images_game_id", "card_images", ["game_id"])


def downgrade() -> None:
    op.drop_index("ix_card_images_game_id", table_name="card_images")
    op.drop_table("card_images")
