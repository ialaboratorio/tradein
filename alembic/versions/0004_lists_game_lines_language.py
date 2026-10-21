"""Lista ligada a un TCG e idioma de las lineas.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trade_lists",
        sa.Column("game_id", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute(
        "UPDATE trade_lists SET game_id = COALESCE("
        "(SELECT game_id FROM trade_lines "
        "WHERE trade_lines.list_id = trade_lists.id "
        "ORDER BY trade_lines.id LIMIT 1), 1)"
    )
    op.add_column(
        "trade_lines",
        sa.Column("language", sa.String(length=2), nullable=False, server_default="es"),
    )


def downgrade() -> None:
    op.drop_column("trade_lines", "language")
    op.drop_column("trade_lists", "game_id")
