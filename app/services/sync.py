import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.config import settings
from app.db import SessionLocal
from app.models import CardPrice, SyncRun, utcnow
from app.services import cardmarket
from sqlalchemy import func, select

logger = logging.getLogger("tradein.sync")

TZ = ZoneInfo(settings.sync_timezone)


def run_full_sync() -> list[dict]:
    results: list[dict] = []
    with httpx.Client(follow_redirects=True, headers={"User-Agent": "tradein/1.0"}) as client:
        with SessionLocal() as db:
            for game_id in settings.game_ids():
                jobs = (
                    ("catalog", cardmarket.sync_game_catalog),
                    ("prices", cardmarket.sync_game_prices),
                )
                for kind, fn in jobs:
                    try:
                        count = fn(client, db, game_id)
                        message = f"{count} registros"
                        ok = True
                    except Exception as exc:
                        message = str(exc)[:500]
                        ok = False
                        logger.error("sync game=%s kind=%s failed: %s", game_id, kind, exc)
                    db.add(SyncRun(game_id=game_id, kind=kind, ok=ok, message=message))
                    db.commit()
                    results.append({"game_id": game_id, "kind": kind, "ok": ok, "message": message})
    return results


def needs_sync() -> bool:
    try:
        with SessionLocal() as db:
            latest = db.scalar(select(func.max(CardPrice.fetched_at)))
    except Exception as exc:
        logger.warning("needs_sync check failed: %s", exc)
        return False
    if latest is None:
        return True
    ttl = timedelta(hours=settings.price_cache_ttl_hours)
    return utcnow() - latest > ttl


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
        if await asyncio.to_thread(needs_sync):
            logger.info("starting initial Cardmarket sync")
            await asyncio.to_thread(run_full_sync)
    except Exception:
        logger.exception("initial sync failed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for item in run_full_sync():
        status = "OK" if item["ok"] else "ERROR"
        print(f"[{status}] game={item['game_id']} {item['kind']}: {item['message']}")
