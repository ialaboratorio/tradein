from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, not_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import GAME_NAMES, Card, CardPrice, Expansion
from app.services.expansions import expansion_names, set_label
from app.services.singles import exclusion_condition

router = APIRouter(prefix="/api/cards", tags=["cards"])


@router.get("/search")
def search_cards(
    q: str = Query(min_length=2, max_length=120),
    game: int | None = None,
    db: Session = Depends(get_db),
):
    term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if not term:
        return []
    pattern = f"%{term}%"
    name_low = func.lower(Card.name)
    priority = case(
        (name_low == q.strip().lower(), 0),
        (name_low.like(f"{term.lower()}%", escape="\\"), 1),
        else_=2,
    )
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
        .outerjoin(
            Expansion,
            and_(
                Expansion.game_id == Card.game_id,
                Expansion.id_expansion == Card.id_expansion,
            ),
        )
        .where(Card.name.ilike(pattern, escape="\\"), not_(exclusion_condition(db, game)))
        .order_by(
            priority,
            Card.name,
            case((Expansion.name.is_(None), 1), else_=0),
            Expansion.name,
            Card.id_product,
        )
        .limit(100)
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
