"""trade_lists trade_lines list_events

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trade_lists",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_sub", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("payment_method", sa.String(length=8), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["user_sub"], ["users.sub"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trade_lists_user_sub", "trade_lists", ["user_sub"])

    op.create_table(
        "trade_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("list_id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("id_product", sa.Integer(), nullable=False),
        sa.Column("card_name", sa.String(length=512), nullable=False),
        sa.Column("id_expansion", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("foil", sa.Boolean(), nullable=False),
        sa.Column("condition", sa.String(length=8), nullable=False),
        sa.Column("unit_low", sa.Float(), nullable=True),
        sa.Column("price_fetched_at", sa.DateTime(), nullable=True),
        sa.Column("unit_final", sa.Float(), nullable=True),
        sa.Column("override_reason", sa.String(length=255), nullable=True),
        sa.Column("price_alert", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["list_id"], ["trade_lists.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trade_lines_list_id", "trade_lines", ["list_id"])

    op.create_table(
        "list_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("list_id", sa.Integer(), nullable=False),
        sa.Column("actor_sub", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["list_id"], ["trade_lists.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_list_events_list_id", "list_events", ["list_id"])


def downgrade() -> None:
    op.drop_index("ix_list_events_list_id", table_name="list_events")
    op.drop_table("list_events")
    op.drop_index("ix_trade_lines_list_id", table_name="trade_lines")
    op.drop_table("trade_lines")
    op.drop_index("ix_trade_lists_user_sub", table_name="trade_lists")
    op.drop_table("trade_lists")
