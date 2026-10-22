from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models import Card, Expansion

NAME_LIKE_EXCLUDED = ("art series:%", "%oversized%")
EXPANSION_LIKE_EXCLUDED = ("%art series%", "%token%", "%oversized%")
EMBLEM_SINGLES = frozenset(
    {
        "emblem of dragon destroyer",
        "emblem of the plunder patroll",
        "emblem of the awakening",
        "emblem of the warmind",
        "emblema oath",
        "emblema salvation",
        "blackwing - vayu the emblem of honor",
        "blackwing - vata the emblem of wandering",
        "dr. julius jumblemorph",
        "leering emblem",
    }
)


def excluded_name(name: str | None) -> bool:
    if not name:
        return False
    low = name.strip().lower()
    if low.startswith("art series:"):
        return True
    if (
        low == "token"
        or low.endswith(" token")
        or low.endswith(" token)")
        or low.startswith("token (")
        or " token (" in low
    ):
        return True
    if "oversized" in low:
        return True
    return "emblem" in low and low not in EMBLEM_SINGLES


def excluded_expansion_name(name: str | None) -> bool:
    if not name:
        return False
    low = name.lower()
    return "art series" in low or "token" in low or "oversized" in low


def excluded_expansion_groups(db: Session, game_id: int | None = None) -> dict[int, list[int]]:
    stmt = select(Expansion.game_id, Expansion.id_expansion).where(
        or_(*[func.lower(Expansion.name).like(p) for p in EXPANSION_LIKE_EXCLUDED])
    )
    if game_id is not None:
        stmt = stmt.where(Expansion.game_id == game_id)
    groups: dict[int, list[int]] = {}
    for game, expansion_id in db.execute(stmt):
        groups.setdefault(game, []).append(expansion_id)
    return groups


def excluded_expansion_ids(db: Session, game_id: int | None = None) -> set[int]:
    return {
        expansion_id
        for ids in excluded_expansion_groups(db, game_id).values()
        for expansion_id in ids
    }


def exclusion_condition(db: Session, game_id: int | None = None) -> ColumnElement[bool]:
    name_low = func.lower(func.trim(Card.name))
    token = or_(
        name_low == "token",
        name_low.like("% token"),
        name_low.like("% token)"),
        name_low.like("token (%"),
        name_low.like("% token (%"),
    )
    emblem = name_low.like("%emblem%") & name_low.not_in(sorted(EMBLEM_SINGLES))
    condition = or_(
        *[name_low.like(p) for p in NAME_LIKE_EXCLUDED], token, emblem
    )
    groups = excluded_expansion_groups(db, game_id)
    expansion_condition = or_(
        *[
            and_(
                Card.id_expansion.is_not(None),
                Card.game_id == game,
                Card.id_expansion.in_(ids),
            )
            for game, ids in groups.items()
            if ids
        ]
    ) if groups else None
    if expansion_condition is not None:
        condition = or_(condition, expansion_condition)
    return condition
