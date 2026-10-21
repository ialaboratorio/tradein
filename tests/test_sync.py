from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models import CardPrice, utcnow
from app.services.sync import stale_game_ids


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    s = TestingSession()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def test_stale_game_ids_checks_each_game(session):
    all_games = settings.game_ids()
    assert stale_game_ids(session) == all_games

    session.add(CardPrice(game_id=22, id_product=1, low=1.0, fetched_at=utcnow()))
    session.commit()
    stale = stale_game_ids(session)
    assert 22 not in stale
    assert stale == [g for g in all_games if g != 22]

    old = utcnow() - timedelta(hours=settings.price_cache_ttl_hours + 2)
    session.add(CardPrice(game_id=1, id_product=2, low=1.0, fetched_at=old))
    session.commit()
    stale = stale_game_ids(session)
    assert 1 in stale
    assert 22 not in stale
    assert len(stale) == len(all_games) - 1


def test_stale_game_ids_fresh_game_not_synced(session):
    for game_id in settings.game_ids():
        session.add(CardPrice(game_id=game_id, id_product=1, low=1.0, fetched_at=utcnow()))
    session.commit()
    assert stale_game_ids(session) == []


def test_run_full_sync_isolates_job_failures(monkeypatch, session):
    from app.models import SyncRun
    from app.services import sync as sync_mod

    def catalog_boom(client, db, game_id):
        from sqlalchemy import text

        db.execute(text("INSERT INTO card_prices (game_id, id_product) VALUES (1, NULL)"))

    def ok(client, db, game_id):
        return 7

    monkeypatch.setattr(sync_mod.cardmarket, "sync_game_catalog", catalog_boom)
    monkeypatch.setattr(sync_mod.expansions, "sync_game_expansions", ok)
    monkeypatch.setattr(sync_mod.cardmarket, "sync_game_prices", ok)
    monkeypatch.setattr(sync_mod, "SessionLocal", lambda: session)

    results = sync_mod.run_full_sync([1, 22])
    assert len(results) == 6
    by_key = {(item["game_id"], item["kind"]): item for item in results}
    assert by_key[(1, "catalog")]["ok"] is False
    assert by_key[(1, "expansions")]["ok"] is True
    assert by_key[(1, "prices")]["ok"] is True
    assert by_key[(22, "catalog")]["ok"] is False
    assert by_key[(22, "expansions")]["ok"] is True
    assert by_key[(22, "prices")]["ok"] is True
    assert session.query(SyncRun).count() == 6
