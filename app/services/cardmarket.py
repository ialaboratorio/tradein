import tempfile

import httpx
import ijson
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Card, CardPrice, utcnow

PRICE_GUIDE_URL = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_{game}.json"
PRODUCT_LIST_URL = "https://downloads.s3.cardmarket.com/productCatalog/productList/products_singles_{game}.json"


def _f(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0 or parsed > 1_000_000:
        return None
    return parsed


def _i(value) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _stream_items(client: httpx.Client, url: str, prefix: str):
    with client.stream("GET", url, timeout=180) as response:
        response.raise_for_status()
        with tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024) as tmp:
            for chunk in response.iter_bytes():
                tmp.write(chunk)
            tmp.seek(0)
            yield from ijson.items(tmp, prefix)


def sync_game_catalog(client: httpx.Client, db: Session, game_id: int) -> int:
    items = _stream_items(client, PRODUCT_LIST_URL.format(game=game_id), "products.item")
    existing = {
        card.id_product: card
        for card in db.scalars(select(Card).where(Card.game_id == game_id))
    }
    added = 0
    for item in items:
        id_product = _i(item.get("idProduct"))
        if id_product is None:
            continue
        name = item.get("name") or ""
        id_expansion = _i(item.get("idExpansion"))
        card = existing.get(id_product)
        if card is None:
            db.add(Card(game_id=game_id, id_product=id_product, name=name, id_expansion=id_expansion))
            added += 1
        else:
            card.name = name
            card.id_expansion = id_expansion
    db.commit()
    return added


def sync_game_prices(client: httpx.Client, db: Session, game_id: int) -> int:
    items = _stream_items(client, PRICE_GUIDE_URL.format(game=game_id), "priceGuides.item")
    existing = {
        price.id_product: price
        for price in db.scalars(select(CardPrice).where(CardPrice.game_id == game_id))
    }
    now = utcnow()
    updated = 0
    for item in items:
        id_product = _i(item.get("idProduct"))
        if id_product is None:
            continue
        values = {
            "low": _f(item.get("low")),
            "low_foil": _f(item.get("low-foil")),
            "trend": _f(item.get("trend")),
            "avg7": _f(item.get("avg7")),
            "source": "cardmarket_s3",
            "fetched_at": now,
        }
        price = existing.get(id_product)
        if price is None:
            db.add(CardPrice(game_id=game_id, id_product=id_product, **values))
        else:
            for key, value in values.items():
                setattr(price, key, value)
        updated += 1
        if updated % 2000 == 0:
            db.flush()
    db.commit()
    return updated
