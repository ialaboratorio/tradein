import gzip
import json
import logging
import os
import re
import tempfile
import threading
import time
import unicodedata
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Card, CardImage, Expansion, utcnow

logger = logging.getLogger("tradein.images")

IMAGES_DIR = Path(__file__).resolve().parents[2] / "data" / "images"

SCRYFALL_BULK_URL = "https://api.scryfall.com/bulk-data"
DOTGG_URL = "https://api.dotgg.gg/cgfw/getcards?game={game}"
YGO_CARDINFO_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
LORCAST_SETS_URL = "https://api.lorcast.com/v0/sets"
LORCAST_SEARCH_URL = "https://api.lorcast.com/v0/cards/search"
POKEMON_CARDS_URL = "https://api.pokemontcg.io/v2/cards"
POKEMON_SETS_URL = "https://api.pokemontcg.io/v2/sets"

USER_AGENT = {"User-Agent": "tradein/1.0"}

SYNC_IMAGE_SOURCES = {1: "scryfall", 6: "pokemon", 18: "dotgg", 22: "dotgg", 24: "dotgg"}
ONDEMAND_SOURCES = {3: "ygoprodeck", 19: "lorcast", 6: "pokemontcg"}

DOTGG_GAMES = {18: "onepiece", 22: "riftbound", 24: "gundam"}

_CARD_CODE_RE = re.compile(r"\(([A-Z0-9]{1,5}-[0-9]{1,4})\)")

REFRESH_HOURS = 168
FAIL_COOLDOWN = timedelta(hours=24)
MAX_IMAGE_BYTES = 8 * 1024 * 1024
DOWNLOAD_TIMEOUT = 20.0
MIN_INTERVAL = {"ygoprodeck": 0.1, "lorcast": 0.15, "pokemontcg": 2.1}

ALLOWED_IMAGE_HOSTS = {
    "cards.scryfall.io",
    "images.ygoprodeck.com",
    "images.onepiecedb.io",
    "static.dotgg.gg",
    "cards.lorcast.io",
    "images.pokemontcg.io",
}

CACHE_OK = "public, max-age=604800"
CACHE_PLACEHOLDER = "no-store"

_EXT_BY_CTYPE = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/avif": "avif",
    "image/gif": "gif",
}
_CTYPE_BY_EXT = {ext: ctype for ctype, ext in _EXT_BY_CTYPE.items()}

def _build_placeholder(width: int, height: int) -> bytes:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" rx="30" fill="#161f2e"/>'
        f'<rect x="20" y="20" width="{width - 40}" height="{height - 40}" rx="20" fill="none" '
        'stroke="#2a3a52" stroke-width="4" stroke-dasharray="14 10"/>'
        f'<g transform="translate({width // 2} {height // 2})">'
        '<rect x="-92" y="-66" width="184" height="132" rx="14" fill="none" '
        'stroke="#44546e" stroke-width="12"/>'
        '<circle cx="-36" cy="-18" r="17" fill="#44546e"/>'
        '<path d="M-76 42 L-16 -6 L46 42 Z" fill="#44546e"/></g></svg>'
    ).encode("utf-8")


PLACEHOLDER_SVG = _build_placeholder(489, 681)
PLACEHOLDER_PENDING_SVG = _build_placeholder(491, 683)

_LAST_CALL: dict[str, float] = {}
_THROTTLE_LOCKS: dict[str, threading.Lock] = {}
_LOCK_GUARD = threading.Lock()

_SLOT_COUNT = 8
_RESOLVE_SLOTS = threading.BoundedSemaphore(_SLOT_COUNT)

_LOCAL_PATH_RE = re.compile(r"^[0-9]{1,12}\.[a-z0-9]{2,5}$")

_LORCAST_SETS_CACHE: dict[str, str] = {}
_LORCAST_SETS_AT = 0.0
_LORCAST_SETS_LOCK = threading.Lock()

_POKEMON_SETS_CACHE: dict[str, str] = {}
_POKEMON_SETS_AT = 0.0
_POKEMON_SETS_LOCK = threading.Lock()


def _placeholder() -> tuple[bytes, str, str]:
    return (PLACEHOLDER_SVG, "image/svg+xml", CACHE_PLACEHOLDER)


def _throttle(source: str) -> None:
    interval = MIN_INTERVAL.get(source, 0.0)
    if interval <= 0:
        return
    with _LOCK_GUARD:
        lock = _THROTTLE_LOCKS.setdefault(source, threading.Lock())
    with lock:
        wait = _LAST_CALL.get(source, 0.0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL[source] = time.monotonic()


_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _retry_delay(resp: httpx.Response | None, attempt: int) -> float:
    if resp is not None:
        retry_after = (resp.headers.get("retry-after") or "").strip()
        if retry_after.isdigit():
            return min(float(retry_after), 10.0)
    return 1.5 * (attempt + 1)


def _http_get(
    url: str,
    params: dict | None = None,
    source: str = "",
    timeout: float = 15.0,
    retries: int = 2,
) -> httpx.Response:
    attempts = max(1, retries + 1)
    resp: httpx.Response | None = None
    for attempt in range(attempts):
        _throttle(source)
        try:
            with httpx.Client(
                follow_redirects=True, headers=USER_AGENT, timeout=timeout
            ) as client:
                resp = client.get(url, params=params)
        except httpx.TransportError:
            if attempt >= attempts - 1:
                raise
            time.sleep(_retry_delay(None, attempt))
            continue
        if resp.status_code not in _RETRY_STATUSES or attempt >= attempts - 1:
            break
        time.sleep(_retry_delay(resp, attempt))
    if resp is None:
        raise httpx.TransportError("sin respuesta")
    return resp


def _scryfall_image_url(card: dict) -> str | None:
    for uris in [card.get("image_uris") or {}] + [
        face.get("image_uris") or {} for face in card.get("card_faces") or []
    ]:
        for key in ("normal", "small", "large"):
            url = uris.get(key)
            if url:
                return url
    return None


def _fetch_scryfall_index(client: httpx.Client, db: Session) -> dict[int, str]:
    resp = client.get(SCRYFALL_BULK_URL, timeout=60)
    resp.raise_for_status()
    entry = next(
        (item for item in resp.json().get("data", []) if item.get("type") == "default_cards"),
        None,
    )
    if entry is None:
        raise RuntimeError("Scryfall no publica el volcado default_cards")
    uri = entry.get("jsonl_download_uri") or entry.get("download_uri")
    if not uri:
        raise RuntimeError("Scryfall bulk sin uri de descarga")
    wanted = set(db.scalars(select(Card.id_product).where(Card.game_id == 1)))
    index: dict[int, str] = {}
    with client.stream("GET", uri, timeout=600) as response:
        response.raise_for_status()
        with tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024) as tmp:
            for chunk in response.iter_bytes():
                tmp.write(chunk)
            tmp.seek(0)
            with gzip.GzipFile(fileobj=tmp, mode="rb") as gz:
                for line in gz:
                    if not line.strip():
                        continue
                    try:
                        card = json.loads(line)
                    except ValueError:
                        continue
                    cmid = card.get("cardmarket_id")
                    if cmid is None:
                        purchase = (card.get("purchase_uris") or {}).get("cardmarket") or ""
                        match = re.search(r"[?&]idProduct=(\d+)", purchase)
                        if not match:
                            continue
                        cmid = match.group(1)
                    try:
                        cmid = int(cmid)
                    except (TypeError, ValueError):
                        continue
                    if cmid not in wanted:
                        continue
                    url = _scryfall_image_url(card)
                    if url:
                        index[cmid] = url
    return index


def _norm_token(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    value = value.replace("&", " and ").casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _set_alias(value: str) -> str:
    key = _norm_token(value)
    for suffix in (
        " jp",
        " fr",
        " id th",
        " id/th",
        " simplified chinese",
        " indonesian and thai",
        " non english",
    ):
        if key.endswith(suffix):
            key = key[: -len(suffix)].strip()
    if key.startswith("ex "):
        key = key[3:]
    if key.startswith("hs "):
        key = key[3:]
    if key == "base set":
        key = "base"
    return key


def _dotgg_image_url(game_id: int, record: dict) -> str | None:
    if game_id in (22, 24):
        return record.get("image")
    pid = str(record.get("id_normal") or record.get("id") or "")
    prefix = pid.split("-", 1)[0] if pid else ""
    return f"https://images.onepiecedb.io/images/{prefix}/{pid}.png" if prefix else None


def _fetch_dotgg_index(client: httpx.Client, db: Session, game_id: int) -> dict[int, str]:
    game = DOTGG_GAMES.get(game_id)
    if game is None:
        return {}
    resp = client.get(DOTGG_URL.format(game=game), timeout=120)
    resp.raise_for_status()
    wanted = dict(
        db.execute(
            select(Card.id_product, Card.name).where(Card.game_id == game_id)
        ).all()
    )
    index: dict[int, str] = {}
    code_records: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for record in resp.json():
        url = _dotgg_image_url(game_id, record)
        cmid = record.get("cmid")
        if url and cmid is not None:
            try:
                cmid_int = int(cmid)
            except (TypeError, ValueError):
                cmid_int = None
            if cmid_int in wanted:
                index[cmid_int] = url
        if game_id not in (18, 24) or not url:
            continue
        pid = str(record.get("id_normal") or record.get("id") or "")
        code = re.sub(r"_p\d+$", "", pid)
        if not code:
            continue
        suffix = pid[len(code):]
        order = int(suffix[2:]) if suffix.startswith("_p") and suffix[2:].isdigit() else 0
        code_records[code].append((order, url))
    if game_id not in (18, 24):
        return index
    code_ids: dict[str, list[int]] = defaultdict(list)
    for id_product, name in wanted.items():
        match = _CARD_CODE_RE.search(name or "")
        if match:
            code_ids[match.group(1)].append(id_product)
    for code, id_products in code_ids.items():
        variants = [url for _, url in sorted(code_records.get(code) or [], key=lambda item: item[0])]
        urls = list(dict.fromkeys(variants))
        if not urls:
            continue
        candidates = [pid for pid in sorted(id_products) if pid not in index]
        if not candidates:
            continue
        if game_id == 18:
            for position, id_product in enumerate(candidates):
                index[id_product] = urls[position] if position < len(urls) else urls[0]
        else:
            index[candidates[0]] = urls[0]
    return index


def _pokemon_sets(client: httpx.Client) -> dict[str, str]:
    global _POKEMON_SETS_AT
    with _POKEMON_SETS_LOCK:
        if _POKEMON_SETS_CACHE and time.monotonic() - _POKEMON_SETS_AT < 86400:
            return _POKEMON_SETS_CACHE
        resp = client.get(POKEMON_SETS_URL, timeout=120)
        resp.raise_for_status()
        mapping: dict[str, str] = {}
        for item in resp.json().get("data") or []:
            set_id = item.get("id")
            if not set_id:
                continue
            mapping.setdefault(_set_alias(item.get("name") or ""), set_id)
            if item.get("ptcgoCode"):
                mapping.setdefault(_set_alias(item["ptcgoCode"]), set_id)
        _POKEMON_SETS_CACHE.clear()
        _POKEMON_SETS_CACHE.update(mapping)
        _POKEMON_SETS_AT = time.monotonic()
        return _POKEMON_SETS_CACHE


def _fetch_pokemon_index(client: httpx.Client, db: Session) -> dict[int, str]:
    resp = client.get(DOTGG_URL.format(game="pokemon"), timeout=180)
    resp.raise_for_status()
    sets = _pokemon_sets(client)
    expansion_sets: dict[int, str] = {}
    for id_expansion, name in db.execute(
        select(Expansion.id_expansion, Expansion.name).where(Expansion.game_id == 6)
    ).all():
        set_id = sets.get(_set_alias(name or ""))
        if set_id:
            expansion_sets[id_expansion] = set_id
    by_name: dict[tuple[str, str], str] = {}
    for record in resp.json():
        set_id = record.get("setId")
        number = record.get("number")
        if not set_id or not number:
            continue
        base = _norm_token(_pokemon_base_name(record.get("name") or ""))
        by_name.setdefault(
            (set_id, base), f"https://images.pokemontcg.io/{set_id}/{number}.png"
        )
    index: dict[int, str] = {}
    for id_product, name, id_expansion in db.execute(
        select(Card.id_product, Card.name, Card.id_expansion).where(Card.game_id == 6)
    ):
        set_id = expansion_sets.get(id_expansion)
        if set_id is None:
            continue
        url = by_name.get((set_id, _norm_token(_pokemon_base_name(name or ""))))
        if url:
            index[id_product] = url
    return index


def _fetch_index(client: httpx.Client, db: Session, game_id: int, source: str) -> dict[int, str]:
    for attempt in range(3):
        try:
            if source == "scryfall":
                return _fetch_scryfall_index(client, db)
            if source == "dotgg":
                return _fetch_dotgg_index(client, db, game_id)
            if source == "pokemon":
                return _fetch_pokemon_index(client, db)
            raise ValueError(f"fuente desconocida: {source}")
        except (ValueError, httpx.HTTPError, RuntimeError) as exc:
            if isinstance(exc, ValueError) and "fuente desconocida" in str(exc):
                raise
            logger.warning(
                "indice de imagenes fallo game=%s intento=%s: %s", game_id, attempt + 1, exc
            )
            if attempt >= 2:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("indice de imagenes no disponible")


def sync_game_images(client: httpx.Client, db: Session, game_id: int) -> int:
    source = SYNC_IMAGE_SOURCES.get(game_id)
    if source is None:
        return 0
    latest = db.scalar(
        select(func.max(CardImage.fetched_at)).where(CardImage.game_id == game_id)
    )
    if latest is not None and (utcnow() - latest).total_seconds() < REFRESH_HOURS * 3600:
        return (
            db.scalar(
                select(func.count())
                .select_from(CardImage)
                .where(CardImage.game_id == game_id)
            )
            or 0
        )
    index = _fetch_index(client, db, game_id, source)
    owned = dict(
        db.execute(select(CardImage.id_product, CardImage.game_id)).all()
    )
    existing = {
        row.id_product: row
        for row in db.scalars(select(CardImage).where(CardImage.game_id == game_id))
    }
    now = utcnow()
    for position, (id_product, url) in enumerate(index.items(), start=1):
        owner = owned.get(id_product)
        if owner is not None and owner != game_id:
            logger.warning(
                "id_product=%s pertenece a game=%s, se omite en game=%s",
                id_product,
                owner,
                game_id,
            )
            continue
        row = existing.get(id_product)
        if row is None:
            db.add(
                CardImage(
                    id_product=id_product,
                    game_id=game_id,
                    source=source,
                    source_url=url,
                    fetched_at=now,
                )
            )
        else:
            row.source_url = url
            row.fetched_at = now
        if position % 2000 == 0:
            db.flush()
    db.commit()
    return len(index)


def _ygo_candidates(name: str) -> list[str]:
    candidates = [name]
    fixed = name.replace('""', '"')
    if fixed != name:
        candidates.append(fixed)
    return candidates


def _resolve_ygoprodeck(card: Card, expansion_name: str | None) -> str | None:
    targets = {candidate.casefold() for candidate in _ygo_candidates(card.name)}
    for candidate in _ygo_candidates(card.name):
        resp = _http_get(YGO_CARDINFO_URL, params={"name": candidate}, source="ygoprodeck")
        if resp.status_code != 200:
            continue
        try:
            data = resp.json().get("data") or []
        except ValueError:
            continue
        for item in data:
            item_name = (item.get("name") or "").strip().casefold()
            if item_name not in targets:
                continue
            images = item.get("card_images") or []
            url = images[0].get("image_url") if images else None
            if url:
                return url
    return None


def _lorcast_sets() -> dict[str, str]:
    global _LORCAST_SETS_CACHE, _LORCAST_SETS_AT
    with _LORCAST_SETS_LOCK:
        if _LORCAST_SETS_CACHE and time.monotonic() - _LORCAST_SETS_AT < REFRESH_HOURS * 3600:
            return dict(_LORCAST_SETS_CACHE)
    try:
        resp = _http_get(LORCAST_SETS_URL, source="lorcast")
    except httpx.HTTPError:
        with _LORCAST_SETS_LOCK:
            return dict(_LORCAST_SETS_CACHE)
    if resp.status_code != 200:
        with _LORCAST_SETS_LOCK:
            return dict(_LORCAST_SETS_CACHE)
    try:
        items = resp.json().get("results") or []
    except ValueError:
        with _LORCAST_SETS_LOCK:
            return dict(_LORCAST_SETS_CACHE)
    mapping = {
        str(item["name"]).strip().casefold(): str(item["code"])
        for item in items
        if item.get("name") and item.get("code")
    }
    with _LORCAST_SETS_LOCK:
        if mapping:
            _LORCAST_SETS_CACHE = mapping
            _LORCAST_SETS_AT = time.monotonic()
        return dict(_LORCAST_SETS_CACHE)


def _lorcast_image(item: dict) -> str | None:
    uris = item.get("image_uris") or {}
    digital = uris.get("digital") or {}
    return digital.get("normal") or digital.get("small") or digital.get("large")


def _resolve_lorcana(card: Card, expansion_name: str | None) -> str | None:
    if expansion_name is not None and not expansion_name.strip():
        return None
    exp_cf = (expansion_name or "").strip().casefold()
    code = _lorcast_sets().get(exp_cf) if exp_cf else None
    candidates = [card.name]
    if " - " in card.name:
        candidates.append(card.name.split(" - ", 1)[0].strip())
    for candidate in candidates:
        queries = [f"{candidate} set:{code}"] if code else []
        queries.append(candidate)
        for query in queries:
            resp = _http_get(LORCAST_SEARCH_URL, params={"q": query}, source="lorcast")
            if resp.status_code != 200:
                continue
            try:
                results = resp.json().get("results") or []
            except ValueError:
                continue
            for item in results:
                item_name = (item.get("name") or "").strip().casefold()
                if item_name != candidate.casefold():
                    continue
                set_info = item.get("set") or {}
                if code:
                    set_ok = str(set_info.get("code") or "") == code
                else:
                    set_ok = not exp_cf or (
                        str(set_info.get("name") or "").strip().casefold() == exp_cf
                    )
                if set_ok:
                    url = _lorcast_image(item)
                    if url:
                        return url
    return None


def _pokemon_base_name(name: str) -> str:
    base = name.split("[", 1)[0].strip().replace('""', '"')
    base = re.sub(r"\s+Lv\.?\s*\d+$", "", base, flags=re.IGNORECASE)
    return re.sub(r"\s*\([^()]*\)$", "", base).strip()


def _resolve_pokemon(card: Card, expansion_name: str | None) -> str | None:
    if expansion_name is not None and not expansion_name.strip():
        return None
    base = _pokemon_base_name(card.name)
    if not base:
        return None
    query = f'name:"{base}"' if '"' not in base else f"name:{base}"
    resp = _http_get(
        POKEMON_CARDS_URL, params={"q": query, "pageSize": 50}, source="pokemontcg"
    )
    if resp.status_code != 200:
        return None
    try:
        data = resp.json().get("data") or []
    except ValueError:
        return None
    name_cf = base.casefold()
    exp_key = _set_alias(expansion_name or "")
    for item in data:
        if (item.get("name") or "").strip().casefold() != name_cf:
            continue
        set_name = str((item.get("set") or {}).get("name") or "")
        if exp_key and _set_alias(set_name) != exp_key:
            continue
        images = item.get("images") or {}
        url = images.get("large") or images.get("small")
        if url:
            return url
    return None


_RESOLVERS = {
    "ygoprodeck": _resolve_ygoprodeck,
    "lorcast": _resolve_lorcana,
    "pokemontcg": _resolve_pokemon,
}


def _expansion_name(db: Session, card: Card) -> str | None:
    if card.id_expansion is None:
        return None
    return db.scalar(
        select(Expansion.name).where(
            Expansion.game_id == card.game_id,
            Expansion.id_expansion == card.id_expansion,
        )
    )


def _safe_resolve(db: Session, card: Card, source: str) -> str | None:
    exp_name = _expansion_name(db, card)
    try:
        return _RESOLVERS[source](card, exp_name)
    except Exception as exc:
        logger.warning("resolucion fallo id_product=%s: %s", card.id_product, exc)
        return None


def _resolve_row(db: Session, id_product: int, now) -> CardImage | None:
    card = db.scalar(select(Card).where(Card.id_product == id_product).limit(1))
    if card is None:
        return None
    source = ONDEMAND_SOURCES.get(card.game_id)
    if source is None:
        return None
    url = _safe_resolve(db, card, source)
    row = CardImage(
        id_product=id_product,
        game_id=card.game_id,
        source=source,
        source_url=url,
        fetched_at=now,
        failed_at=None if url else now,
    )
    db.add(row)
    db.commit()
    return row if url else None


def _re_resolve(db: Session, row: CardImage, now) -> bool:
    card = db.scalar(
        select(Card)
        .where(Card.id_product == row.id_product, Card.game_id == row.game_id)
        .limit(1)
    )
    url = _safe_resolve(db, card, row.source) if card is not None else None
    row.source_url = url
    row.fetched_at = now
    row.failed_at = None if url else now
    db.commit()
    return bool(url)


def _download(url: str) -> tuple[bytes, str]:
    current = url
    redirects = 0
    attempts = 0
    while True:
        parts = urlsplit(current)
        scheme = (parts.scheme or "").lower()
        host = (parts.hostname or "").lower()
        if scheme != "https" or host not in ALLOWED_IMAGE_HOSTS:
            raise ValueError(f"host de imagen no permitido: {host}")
        with httpx.Client(
            follow_redirects=False, headers=USER_AGENT, timeout=DOWNLOAD_TIMEOUT
        ) as client:
            with client.stream("GET", current) as response:
                status = response.status_code
                if status in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("redireccion sin destino")
                    redirects += 1
                    if redirects > 5:
                        raise ValueError("demasiadas redirecciones")
                    current = urljoin(current, location)
                    continue
                if status in _RETRY_STATUSES:
                    attempts += 1
                    if attempts > 2:
                        raise ValueError(f"respuesta no admitida: {status}")
                    retry_after = (response.headers.get("retry-after") or "").strip()
                    delay = (
                        min(float(retry_after), 10.0)
                        if retry_after.isdigit()
                        else 1.5 * attempts
                    )
                    time.sleep(delay)
                    continue
                if status >= 400:
                    raise ValueError(f"respuesta no admitida: {status}")
                content_type = (
                    (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                )
                if content_type not in _EXT_BY_CTYPE:
                    raise ValueError(f"content-type no admitido: {content_type}")
                buffer = bytearray()
                for chunk in response.iter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > MAX_IMAGE_BYTES:
                        raise ValueError("imagen demasiado grande")
        if not buffer:
            raise ValueError("imagen vacia")
        return bytes(buffer), content_type


def _save_file(filename: str, content: bytes) -> Path:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    target = IMAGES_DIR / filename
    tmp = IMAGES_DIR / (
        f"{filename}.{os.getpid()}.{threading.get_ident()}.{uuid4().hex[:8]}.part"
    )
    try:
        tmp.write_bytes(content)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    try:
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        if not target.exists():
            raise
    return target


def _serve_local(row: CardImage, db: Session) -> tuple[bytes, str, str] | None:
    if not row.local_path:
        return None
    if not _LOCAL_PATH_RE.fullmatch(row.local_path):
        row.local_path = None
        db.commit()
        return None
    base = IMAGES_DIR.resolve()
    path = (IMAGES_DIR / row.local_path).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        row.local_path = None
        db.commit()
        return None
    content_type = row.content_type or _CTYPE_BY_EXT.get(path.suffix.lstrip("."), "")
    if content_type not in _EXT_BY_CTYPE:
        row.local_path = None
        db.commit()
        return None
    return (path.read_bytes(), content_type, CACHE_OK)


def serve_cached(db: Session, id_product: int) -> tuple[bytes, str, str] | None:
    try:
        return _serve_cached(db, id_product)
    except OperationalError as exc:
        db.rollback()
        logger.warning(
            "sqlite ocupada al servir cache id_product=%s: %s", id_product, exc
        )
        return None


def _serve_cached(db: Session, id_product: int) -> tuple[bytes, str, str] | None:
    now = utcnow()
    row = db.get(CardImage, id_product)
    if row is None:
        return None
    served = _serve_local(row, db)
    if served is not None:
        return served
    if row.failed_at is not None and now - row.failed_at < FAIL_COOLDOWN:
        return _placeholder()
    if row.source_url is None and row.source not in ONDEMAND_SOURCES.values():
        return _placeholder()
    return None


def placeholder(pending: bool = False) -> tuple[bytes, str, str]:
    svg = PLACEHOLDER_PENDING_SVG if pending else PLACEHOLDER_SVG
    return (svg, "image/svg+xml", CACHE_PLACEHOLDER)


def try_acquire_slot() -> bool:
    return _RESOLVE_SLOTS.acquire(blocking=False)


def release_slot() -> None:
    _RESOLVE_SLOTS.release()


def resolve_in_background(id_product: int) -> None:
    if not _RESOLVE_SLOTS.acquire(blocking=False):
        return
    try:
        with SessionLocal() as db:
            get_image(db, id_product)
    except Exception as exc:
        logger.warning(
            "resolucion en segundo plano fallo id_product=%s: %s", id_product, exc
        )
    finally:
        _RESOLVE_SLOTS.release()


def get_image(db: Session, id_product: int) -> tuple[bytes, str, str]:
    try:
        return _get_image(db, id_product)
    except OperationalError as exc:
        db.rollback()
        logger.warning(
            "sqlite ocupada, se reintenta luego id_product=%s: %s", id_product, exc
        )
        return placeholder(pending=True)


def _get_image(db: Session, id_product: int) -> tuple[bytes, str, str]:
    ready = serve_cached(db, id_product)
    if ready is not None:
        return ready
    now = utcnow()
    row = db.get(CardImage, id_product)
    if row is None:
        try:
            row = _resolve_row(db, id_product, now)
        except IntegrityError:
            db.rollback()
            row = db.get(CardImage, id_product)
        if row is None:
            return _placeholder()
    elif row.source_url is None:
        if row.source not in ONDEMAND_SOURCES.values() or not _re_resolve(db, row, now):
            return _placeholder()
    if row.failed_at is not None and now - row.failed_at < FAIL_COOLDOWN:
        return _placeholder()
    if not row.source_url:
        return _placeholder()
    try:
        content, content_type = _download(row.source_url)
        filename = f"{row.id_product}.{_EXT_BY_CTYPE.get(content_type, 'img')}"
        _save_file(filename, content)
    except Exception as exc:
        logger.warning("descarga de imagen fallo id_product=%s: %s", id_product, exc)
        row.failed_at = utcnow()
        db.commit()
        return _placeholder()
    row.local_path = filename
    row.content_type = content_type
    row.downloaded_at = utcnow()
    row.failed_at = None
    db.commit()
    return (content, content_type, CACHE_OK)
