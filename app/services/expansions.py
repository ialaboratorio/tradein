import logging
import re
import tempfile
from collections import defaultdict

import httpx
import ijson
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Expansion, utcnow
from app.services.cardmarket import _stream_items

logger = logging.getLogger("tradein.expansions")

NONSINGLES_URL = "https://downloads.s3.cardmarket.com/productCatalog/productList/products_nonsingles_{game}.json"
MTGJSON_URL = "https://mtgjson.com/api/v5/SetList.json"

REFRESH_HOURS = 168

_SUFFIXES = [
    "Booster Display Case",
    "Booster Display",
    "Booster Box Display",
    "Booster Box Case",
    "Booster Box",
    "Booster Bundle",
    "Booster Pack Display",
    "Booster Case",
    "Booster Tower",
    "Sleeved Booster Pack",
    "Sleeved Booster",
    "Intro Pack Booster",
    "Booster",
    "Intro Pack",
    "Starter Deck",
    "Theme Deck",
    "Event Deck",
    "Structure Deck",
    "Tournament Pack",
    "Special Edition",
    "Starter Set",
    "Elite Trainer Box",
    "ETB",
    "Collection Box",
    "Collector Box",
    "Gift Bundle",
    "Bundle",
    "Display",
    "Case",
    "Box Set",
    "Box",
    "Pack",
    "Tin",
    "Blister",
    "Hanger Pack",
    "Hanger",
    "Fat Pack",
    "Deck Box",
]

_GENERIC_CANDIDATES = {
    "booster",
    "booster box",
    "booster display",
    "booster case",
    "booster bundle",
    "sleeved booster",
    "display",
    "box",
    "case",
    "pack",
    "tin",
    "bundle",
}

_MARKER_RE = re.compile(r"\s*\([^()]{1,40}\)\s*$")
_COLON_SET_RE = re.compile(
    r"^(.+?):\s+(?:Common|Uncommon|Rare|Epic|Main|Master|Full|Base|Complete|"
    r"Trainers[^:]*|Champions?|Pokémon|Pokemon)\s+Set$",
    re.I,
)
_BOOSTER_RE = re.compile(r"\bbooster\b", re.I)


def _strip_generic(name: str) -> str:
    s = name.strip()
    marker = ""
    match = _MARKER_RE.search(s)
    if match:
        marker = " " + match.group(0).strip()
        s = s[: match.start()].strip()
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            found = re.search(r"\s+" + re.escape(suffix) + r"\s*$", s, re.I)
            if found and found.start() > 0:
                s = s[: found.start()].strip(" -:(")
                changed = True
                break
    return (s + marker).strip() if s else ""


def extract_names(items: list[dict]) -> dict[int, str]:
    by_booster: dict[int, list[str]] = defaultdict(list)
    by_colon: dict[int, set[str]] = defaultdict(set)
    for item in items:
        raw_exp = item.get("idExpansion")
        if raw_exp is None:
            continue
        try:
            exp_id = int(raw_exp)
        except (TypeError, ValueError):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        category = str(item.get("categoryName") or "").lower()
        colon = _COLON_SET_RE.match(name)
        if colon:
            by_colon[exp_id].add(colon.group(1).strip())
        if "booster" in category or _BOOSTER_RE.search(name):
            by_booster[exp_id].append(name)

    names: dict[int, str] = {}
    for exp_id, products in by_booster.items():
        candidates = []
        for product in products:
            stripped = _strip_generic(product)
            if len(stripped) >= 3 and stripped.lower() not in _GENERIC_CANDIDATES:
                candidates.append(stripped)
        if candidates:
            candidates.sort(key=len)
            names[exp_id] = candidates[0][:255]
    for exp_id, bases in by_colon.items():
        if exp_id not in names and bases:
            base = sorted(bases, key=len)[0]
            if base.lower() not in _GENERIC_CANDIDATES:
                names[exp_id] = base[:255]
    return names


def _load_mtgjson(client: httpx.Client) -> dict[int, str]:
    mapping: dict[int, str] = {}
    with client.stream("GET", MTGJSON_URL, timeout=300) as response:
        response.raise_for_status()
        with tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024) as tmp:
            for chunk in response.iter_bytes():
                tmp.write(chunk)
            tmp.seek(0)
            for entry in ijson.items(tmp, "data.item"):
                mcm_id = entry.get("mcmId")
                name = entry.get("name")
                if mcm_id and name:
                    mapping.setdefault(int(mcm_id), str(name))
    return mapping


def sync_game_expansions(client: httpx.Client, db: Session, game_id: int) -> int:
    latest = db.scalar(
        select(func.max(Expansion.fetched_at)).where(Expansion.game_id == game_id)
    )
    if latest is not None and (utcnow() - latest).total_seconds() < REFRESH_HOURS * 3600:
        return db.scalar(
            select(func.count()).select_from(Expansion).where(Expansion.game_id == game_id)
        ) or 0

    items = list(_stream_items(client, NONSINGLES_URL.format(game=game_id), "products.item"))
    names = extract_names(items)
    source = "nonsingles"
    if game_id == 1:
        try:
            for mcm_id, name in _load_mtgjson(client).items():
                names.setdefault(mcm_id, name)
            source = "nonsingles+mtgjson"
        except Exception as exc:
            logger.warning("MTGJSON load failed, using nonsingles only: %s", exc)
            source = "nonsingles"

    db.execute(delete(Expansion).where(Expansion.game_id == game_id))
    now = utcnow()
    for exp_id, name in names.items():
        db.add(
            Expansion(
                game_id=game_id,
                id_expansion=exp_id,
                name=name,
                source=source,
                fetched_at=now,
            )
        )
    db.commit()
    return len(names)


def expansion_names(db: Session, pairs: set[tuple[int, int]]) -> dict[tuple[int, int], str]:
    names: dict[tuple[int, int], str] = {}
    for game_id in {game for game, _ in pairs}:
        ids = [exp for g, exp in pairs if g == game_id]
        if not ids:
            continue
        stmt = select(Expansion.id_expansion, Expansion.name).where(
            Expansion.game_id == game_id, Expansion.id_expansion.in_(ids)
        )
        for exp_id, name in db.execute(stmt):
            names[(game_id, exp_id)] = name
    return names


def set_label(names: dict[tuple[int, int], str], game_id: int, id_expansion: int | None) -> str | None:
    if id_expansion is None:
        return None
    return names.get((game_id, id_expansion)) or f"Set #{id_expansion}"
