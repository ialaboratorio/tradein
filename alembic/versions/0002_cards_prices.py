"""cards card_prices sync_runs

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cards",
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("id_product", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("id_expansion", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("game_id", "id_product"),
    )
    op.create_index("ix_cards_name", "cards", ["name"])

    op.create_table(
        "card_prices",
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("id_product", sa.Integer(), nullable=False),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("low_foil", sa.Float(), nullable=True),
        sa.Column("trend", sa.Float(), nullable=True),
        sa.Column("avg7", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("game_id", "id_product"),
    )

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("message", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("sync_runs")
    op.drop_table("card_prices")
    op.drop_index("ix_cards_name", table_name="cards")
    op.drop_table("cards")
