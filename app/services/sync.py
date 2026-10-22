import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.config import settings
from app.db import SessionLocal
from app.models import CardPrice, SyncRun, utcnow
from app.services import cardmarket, expansions, images
from sqlalchemy import func, select
from sqlalchemy.orm import Session

logger = logging.getLogger("tradein.sync")

TZ = ZoneInfo(settings.sync_timezone)


def run_full_sync(game_ids: list[int] | None = None) -> list[dict]:
    games = settings.game_ids() if game_ids is None else game_ids
    results: list[dict] = []
    with httpx.Client(follow_redirects=True, headers={"User-Agent": "tradein/1.0"}) as client:
        with SessionLocal() as db:
            for game_id in games:
                jobs = (
                    ("catalog", cardmarket.sync_game_catalog),
                    ("expansions", expansions.sync_game_expansions),
                    ("prices", cardmarket.sync_game_prices),
                )
                if game_id in images.SYNC_IMAGE_SOURCES:
                    jobs = jobs + (("images", images.sync_game_images),)
                for kind, fn in jobs:
                    try:
                        count = fn(client, db, game_id)
                        message = f"{count} registros"
                        ok = True
                    except Exception as exc:
                        db.rollback()
                        message = str(exc)[:500]
                        ok = False
                        logger.error("sync game=%s kind=%s failed: %s", game_id, kind, exc)
                    db.add(SyncRun(game_id=game_id, kind=kind, ok=ok, message=message))
                    db.commit()
                    results.append({"game_id": game_id, "kind": kind, "ok": ok, "message": message})
    return results


def stale_game_ids(db: Session | None = None) -> list[int]:
    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        rows = db.execute(
            select(CardPrice.game_id, func.max(CardPrice.fetched_at)).group_by(CardPrice.game_id)
        ).all()
    except Exception as exc:
        logger.warning("stale games check failed: %s", exc)
        return []
    finally:
        if own_session:
            db.close()
    latest = {game_id: fetched_at for game_id, fetched_at in rows}
    ttl = timedelta(hours=settings.price_cache_ttl_hours)
    now = utcnow()
    return [
        game_id
        for game_id in settings.game_ids()
        if latest.get(game_id) is None or now - latest[game_id] > ttl
    ]


def _seconds_until(sync_time: str) -> float:
    now = datetime.now(TZ)
    hour, minute = (int(part) for part in sync_time.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def scheduler_loop():
    while True:
        delay = _seconds_until(settings.sync_time)
        logger.info("next Cardmarket sync in %.0f seconds", delay)
        await asyncio.sleep(delay)
        try:
            await asyncio.to_thread(run_full_sync)
        except Exception:
            logger.exception("scheduled sync failed")


async def initial_sync_if_stale():
    try:
        stale = await asyncio.to_thread(stale_game_ids)
        if stale:
            logger.info("starting initial Cardmarket sync for games %s", stale)
            await asyncio.to_thread(run_full_sync, stale)
    except Exception:
        logger.exception("initial sync failed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for item in run_full_sync():
        status = "OK" if item["ok"] else "ERROR"
        print(f"[{status}] game={item['game_id']} {item['kind']}: {item['message']}")
