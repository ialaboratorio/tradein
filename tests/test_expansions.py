import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Expansion
from app.services.expansions import (
    extract_names,
    expansion_names,
    set_label,
    sync_game_expansions,
)

NONSINGLES_FIXTURE = {
    "version": 1,
    "createdAt": "2026-10-21T02:45:00+0200",
    "products": [
        {
            "idProduct": 501,
            "name": "Lightning Bolt Booster",
            "idCategory": 2,
            "idExpansion": 209,
            "categoryName": "Magic Booster",
        },
        {
            "idProduct": 502,
            "name": "Secret Lair Display",
            "idCategory": 3,
            "idExpansion": 210,
            "categoryName": "Magic Display",
        },
        {
            "idProduct": 503,
            "name": "Alpha: Common Set",
            "idCategory": 4,
            "idExpansion": 211,
            "categoryName": "MtG Set",
        },
        {
            "idProduct": 504,
            "name": "Paramount War Booster (Non-English)",
            "idCategory": 5,
            "idExpansion": 5263,
            "categoryName": "One Piece Booster",
        },
        {
            "idProduct": 505,
            "name": "Welcome Deck Booster (V.2)",
            "idCategory": 6,
            "idExpansion": 212,
            "categoryName": "Magic Booster",
        },
    ],
}


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


def _client(payloads: dict[str, dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payloads[request.url.path]).encode())

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_extract_names_booster_and_colon_set():
    names = extract_names(NONSINGLES_FIXTURE["products"])
    assert names[209] == "Lightning Bolt"
    assert 210 not in names
    assert names[211] == "Alpha"
    assert names[5263] == "Paramount War (Non-English)"
    assert names[212] == "Welcome Deck (V.2)"


def test_sync_game_expansions_and_freshness(session):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=json.dumps(NONSINGLES_FIXTURE).encode())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    added = sync_game_expansions(client, session, 19)
    assert added == 4
    assert calls == 1

    rows = session.query(Expansion).filter_by(game_id=19).all()
    assert {row.id_expansion for row in rows} == {209, 211, 212, 5263}
    assert rows[0].source == "nonsingles"

    again = sync_game_expansions(client, session, 19)
    assert again == 4
    assert calls == 1


def test_sync_game_expansions_failure_preserves_previous_names(session):
    from datetime import timedelta

    from app.models import utcnow

    from app.services.expansions import NONSINGLES_URL

    path = NONSINGLES_URL.format(game=19).replace("https://downloads.s3.cardmarket.com", "")
    ok_client = _client({path: NONSINGLES_FIXTURE})
    sync_game_expansions(ok_client, session, 19)
    assert session.query(Expansion).count() == 4

    for row in session.query(Expansion).all():
        row.fetched_at = utcnow() - timedelta(hours=999)
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    with pytest.raises(httpx.HTTPStatusError):
        sync_game_expansions(httpx.Client(transport=httpx.MockTransport(handler)), session, 19)
    assert session.query(Expansion).count() == 4


def test_sync_game_expansions_mtgjson_fills_gaps(session):
    from app.services.expansions import MTGJSON_URL, NONSINGLES_URL

    nonsingles_path = NONSINGLES_URL.format(game=1).replace(
        "https://downloads.s3.cardmarket.com", ""
    )
    payloads = {
        nonsingles_path: NONSINGLES_FIXTURE,
        MTGJSON_URL.replace("https://mtgjson.com", ""): {
            "data": [
                {"name": "Core Set 2021", "code": "m21", "mcmId": 209},
                {"name": "Fifth Edition", "code": "5ed", "mcmId": 74},
            ]
        },
    }
    sync_game_expansions(_client(payloads), session, 1)
    by_id = {row.id_expansion: row for row in session.query(Expansion).filter_by(game_id=1)}
    assert by_id[209].name == "Lightning Bolt"
    assert by_id[209].source == "nonsingles+mtgjson"
    assert by_id[74].name == "Fifth Edition"
    assert 210 not in by_id


def test_expansion_names_and_labels(session):
    session.add(Expansion(game_id=1, id_expansion=10, name="Alpha"))
    session.add(Expansion(game_id=19, id_expansion=20, name="The First Chapter"))
    session.commit()

    pairs = {(1, 10), (1, 99), (19, 20)}
    names = expansion_names(session, pairs)
    assert names == {(1, 10): "Alpha", (19, 20): "The First Chapter"}
    assert set_label(names, 1, 10) == "Alpha"
    assert set_label(names, 1, 99) == "Set #99"
    assert set_label(names, 1, None) is None
