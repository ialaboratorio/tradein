from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import GAME_NAMES, Card, CardPrice
from app.services.expansions import expansion_names, set_label

router = APIRouter(prefix="/api/cards", tags=["cards"])


@router.get("/search")
def search_cards(
    q: str = Query(min_length=2, max_length=120),
    game: int | None = None,
    db: Session = Depends(get_db),
):
    term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{term}%"
    stmt = (
        select(Card, CardPrice)
        .join(
            CardPrice,
            and_(
                CardPrice.game_id == Card.game_id,
                CardPrice.id_product == Card.id_product,
            ),
            isouter=True,
        )
        .where(Card.name.ilike(pattern, escape="\\"))
        .order_by(Card.name, Card.id_product)
        .limit(25)
    )
    if game is not None:
        stmt = stmt.where(Card.game_id == game)
    rows = db.execute(stmt).all()
    names = expansion_names(
        db, {(card.game_id, card.id_expansion) for card, _ in rows if card.id_expansion is not None}
    )
    return [
        {
            "game_id": card.game_id,
            "game_name": GAME_NAMES.get(card.game_id, str(card.game_id)),
            "id_product": card.id_product,
            "name": card.name,
            "id_expansion": card.id_expansion,
            "expansion_name": names.get((card.game_id, card.id_expansion)),
            "set_label": set_label(names, card.game_id, card.id_expansion),
            "category_name": card.category_name,
            "low": price.low if price else None,
            "low_foil": price.low_foil if price else None,
            "has_price": bool(price and (price.low is not None or price.low_foil is not None)),
        }
        for card, price in rows
    ]
