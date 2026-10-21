from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


GAME_NAMES = {
    1: "Magic",
    3: "Yu-Gi-Oh!",
    6: "Pokémon",
    18: "One Piece",
    19: "Lorcana",
    22: "Riftbound",
}

CONDITIONS = {
    "nm": "Casi Perfecta (NM)",
    "ex": "Excelente (EX)",
    "good": "Buena (GOOD)",
    "played": "Jugada (PLAYED)",
    "poor": "Pobre (POOR)",
}

LANGUAGES = {
    "es": "Español",
    "en": "Inglés",
}


class User(Base):
    __tablename__ = "users"

    sub: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    picture: Mapped[str | None] = mapped_column(String(512))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (PrimaryKeyConstraint("game_id", "id_product"),)

    game_id: Mapped[int] = mapped_column(Integer)
    id_product: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(512), index=True)
    id_expansion: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CardPrice(Base):
    __tablename__ = "card_prices"
    __table_args__ = (PrimaryKeyConstraint("game_id", "id_product"),)

    game_id: Mapped[int] = mapped_column(Integer)
    id_product: Mapped[int] = mapped_column(Integer)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_foil: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg7: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="cardmarket_s3")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TradeList(Base):
    __tablename__ = "trade_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_sub: Mapped[str] = mapped_column(ForeignKey("users.sub"), index=True)
    game_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), default="borrador")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[list["TradeLine"]] = relationship(
        back_populates="trade_list", cascade="all, delete-orphan"
    )
    events: Mapped[list["ListEvent"]] = relationship(
        back_populates="trade_list", cascade="all, delete-orphan"
    )
    owner: Mapped[User] = relationship(foreign_keys=[user_sub])


class TradeLine(Base):
    __tablename__ = "trade_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("trade_lists.id", ondelete="CASCADE"), index=True)
    game_id: Mapped[int] = mapped_column(Integer)
    id_product: Mapped[int] = mapped_column(Integer)
    card_name: Mapped[str] = mapped_column(String(512))
    id_expansion: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    foil: Mapped[bool] = mapped_column(Boolean, default=False)
    condition: Mapped[str] = mapped_column(String(8), default="nm")
    language: Mapped[str] = mapped_column(String(2), nullable=False, default="es")
    unit_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    unit_final: Mapped[float | None] = mapped_column(Float, nullable=True)
    override_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price_alert: Mapped[bool] = mapped_column(Boolean, default=False)

    trade_list: Mapped[TradeList] = relationship(back_populates="lines")


class ListEvent(Base):
    __tablename__ = "list_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("trade_lists.id", ondelete="CASCADE"), index=True)
    actor_sub: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    trade_list: Mapped[TradeList] = relationship(back_populates="events")
