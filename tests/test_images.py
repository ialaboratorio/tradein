import gzip
import json
import types
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.models import Card, CardImage, Expansion, utcnow
from app.services import images as images_mod

IMAGE_BYTES = b"\xff\xd8\xff\xe0fakejpegdata"
IMAGE_CTYPE = "image/jpeg"


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


@pytest.fixture(autouse=True)
def images_env(tmp_path, monkeypatch):
    monkeypatch.setattr(images_mod, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(images_mod, "MIN_INTERVAL", {})
    monkeypatch.setattr(images_mod, "_LAST_CALL", {})
    monkeypatch.setattr(images_mod, "_LORCAST_SETS_CACHE", {})
    monkeypatch.setattr(images_mod, "_LORCAST_SETS_AT", 0.0)
    return tmp_path


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def _patch_client(monkeypatch, handler):
    real_client = httpx.Client

    def client_factory(**kwargs):
        kwargs["transport"] = _mock_transport(handler)
        return real_client(**kwargs)

    fake = types.SimpleNamespace(
        Client=client_factory,
        HTTPError=httpx.HTTPError,
        TransportError=httpx.TransportError,
    )
    monkeypatch.setattr(images_mod, "httpx", fake)


def test_sync_scryfall_index_and_freshness(session):
    session.add(Card(game_id=1, id_product=101, name="Lightning Bolt"))
    session.add(Card(game_id=1, id_product=102, name="Sol Ring"))
    session.commit()

    jsonl = "\n".join(
        [
            json.dumps(
                {
                    "cardmarket_id": 101,
                    "image_uris": {"normal": "https://cards.scryfall.io/normal/a.jpg"},
                }
            ),
            json.dumps(
                {
                    "cardmarket_id": 999,
                    "image_uris": {"normal": "https://cards.scryfall.io/normal/b.jpg"},
                }
            ),
            json.dumps(
                {
                    "purchase_uris": {"cardmarket": "https://example/?idProduct=102"},
                    "image_uris": {"normal": "https://cards.scryfall.io/normal/c.jpg"},
                }
            ),
            json.dumps({"name": "sin imagen"}),
        ]
    )
    payload = gzip.compress((jsonl + "\n").encode())
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/bulk-data":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "type": "default_cards",
                            "jsonl_download_uri": "https://data.scryfall.io/default-cards/x.jsonl.gz",
                        }
                    ]
                },
            )
        return httpx.Response(200, content=payload, headers={"content-type": "application/gzip"})

    client = httpx.Client(transport=_mock_transport(handler))
    added = images_mod.sync_game_images(client, session, 1)
    assert added == 2
    rows = {row.id_product: row for row in session.query(CardImage).all()}
    assert rows[101].source_url == "https://cards.scryfall.io/normal/a.jpg"
    assert rows[102].source_url == "https://cards.scryfall.io/normal/c.jpg"
    assert rows[101].source == "scryfall"
    assert 999 not in rows
    first_calls = list(calls)

    again = images_mod.sync_game_images(client, session, 1)
    assert again == 2
    assert calls == first_calls


def test_sync_dotgg_indexes(session):
    session.add(Card(game_id=18, id_product=77, name="Carta OP 1"))
    session.add(Card(game_id=18, id_product=88, name="Carta OP 2"))
    session.add(Card(game_id=22, id_product=555, name="Carta RB 1"))
    session.commit()

    records_op = [
        {"cmid": 77, "id": "OP01-001"},
        {"cmid": 88, "id": "P-135"},
        {"cmid": 999, "id": "OP01-003"},
        {"id": "OP01-002"},
    ]
    records_rb = [{"cmid": 555, "id": "UNL-1", "image": "https://static.dotgg.gg/riftbound/cards/UNL-1.webp"}]

    def handler(request: httpx.Request) -> httpx.Response:
        if "onepiece" in str(request.url):
            return httpx.Response(200, json=records_op)
        return httpx.Response(200, json=records_rb)

    client = httpx.Client(transport=_mock_transport(handler))
    assert images_mod.sync_game_images(client, session, 18) == 2
    rows = {row.id_product: row for row in session.query(CardImage).all()}
    assert rows[77].source_url == "https://images.onepiecedb.io/images/OP01/OP01-001.png"
    assert rows[88].source_url == "https://images.onepiecedb.io/images/P/P-135.png"
    assert 999 not in rows

    assert images_mod.sync_game_images(client, session, 22) == 1
    row = session.query(CardImage).filter_by(id_product=555).one()
    assert row.source_url == "https://static.dotgg.gg/riftbound/cards/UNL-1.webp"
    assert row.source == "dotgg"


def test_get_image_downloads_then_serves_from_disk(session, monkeypatch):
    session.add(
        CardImage(
            id_product=101,
            game_id=1,
            source="scryfall",
            source_url="https://cards.scryfall.io/normal/a.jpg",
            fetched_at=utcnow(),
        )
    )
    session.commit()

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200, content=IMAGE_BYTES, headers={"content-type": IMAGE_CTYPE}
        )

    _patch_client(monkeypatch, handler)

    content, content_type, cache = images_mod.get_image(session, 101)
    assert content == IMAGE_BYTES
    assert content_type == IMAGE_CTYPE
    assert cache == images_mod.CACHE_OK

    row = session.query(CardImage).filter_by(id_product=101).one()
    assert row.local_path == "101.jpg"
    assert row.downloaded_at is not None
    saved = images_mod.IMAGES_DIR / "101.jpg"
    assert saved.is_file()
    assert saved.read_bytes() == IMAGE_BYTES
    assert len(calls) == 1

    content_again, ctype_again, _ = images_mod.get_image(session, 101)
    assert content_again == IMAGE_BYTES
    assert ctype_again == IMAGE_CTYPE
    assert len(calls) == 1


def test_get_image_placeholder_for_unknown_product(session):
    content, content_type, cache = images_mod.get_image(session, 424242)
    assert content == images_mod.PLACEHOLDER_SVG
    assert content_type == "image/svg+xml"
    assert cache == "no-store"


def test_get_image_blocks_disallowed_host(session):
    session.add(
        CardImage(
            id_product=202,
            game_id=1,
            source="scryfall",
            source_url="https://evil.example.com/card.jpg",
            fetched_at=utcnow(),
        )
    )
    session.commit()

    content, content_type, _ = images_mod.get_image(session, 202)
    assert content_type == "image/svg+xml"
    row = session.query(CardImage).filter_by(id_product=202).one()
    assert row.failed_at is not None
    assert row.local_path is None


def test_on_demand_resolve_ygoprodeck(session, monkeypatch):
    session.add(Card(game_id=3, id_product=500, name="Dark Magician"))
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=IMAGE_BYTES, headers={"content-type": IMAGE_CTYPE}
        )

    _patch_client(monkeypatch, handler)

    resolver_calls = []

    def fake_http_get(url, params=None, source="", timeout=30.0):
        resolver_calls.append((url, params))
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": "Dark Magician",
                        "card_images": [
                            {"image_url": "https://images.ygoprodeck.com/images/cards/1.jpg"}
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(images_mod, "_http_get", fake_http_get)

    content, content_type, _ = images_mod.get_image(session, 500)
    assert content == IMAGE_BYTES
    assert content_type == IMAGE_CTYPE
    assert len(resolver_calls) == 1

    row = session.query(CardImage).filter_by(id_product=500).one()
    assert row.source == "ygoprodeck"
    assert row.source_url == "https://images.ygoprodeck.com/images/cards/1.jpg"
    assert row.failed_at is None


def test_on_demand_miss_is_negative_cached(session, monkeypatch):
    session.add(Card(game_id=6, id_product=600, name="Charizard"))
    session.add(
        Expansion(game_id=6, id_expansion=4, name="Gym Challenge")
    )
    session.commit()

    resolver_calls = []

    def fake_http_get(url, params=None, source="", timeout=30.0):
        resolver_calls.append(url)
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(images_mod, "_http_get", fake_http_get)

    content, content_type, cache = images_mod.get_image(session, 600)
    assert content_type == "image/svg+xml"
    assert cache == "no-store"
    assert len(resolver_calls) == 1

    row = session.query(CardImage).filter_by(id_product=600).one()
    assert row.source_url is None
    assert row.failed_at is not None

    images_mod.get_image(session, 600)
    assert len(resolver_calls) == 1

    row.failed_at = utcnow() - timedelta(hours=25)
    session.commit()
    images_mod.get_image(session, 600)
    assert len(resolver_calls) == 2


def test_resolve_lorcana_matches_set_and_short_name(session, monkeypatch):
    session.add(Card(game_id=19, id_product=700, name="Maui - Demigod"))
    session.commit()

    monkeypatch.setattr(
        images_mod, "_lorcast_sets", lambda: {"the first chapter": "1"}
    )

    queries = []

    def fake_http_get(url, params=None, source="", timeout=30.0):
        query = (params or {}).get("q", "")
        queries.append(query)
        if "Maui - Demigod" in query:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "name": "Maui",
                        "set": {"code": "1", "name": "The First Chapter"},
                        "image_uris": {
                            "digital": {"normal": "https://cards.lorcast.io/card/x.avif"}
                        },
                    },
                    {
                        "name": "Maui",
                        "set": {"code": "5", "name": "Azurite Sea"},
                        "image_uris": {
                            "digital": {"normal": "https://cards.lorcast.io/card/y.avif"}
                        },
                    },
                ]
            },
        )

    monkeypatch.setattr(images_mod, "_http_get", fake_http_get)

    card = session.query(Card).filter_by(id_product=700).one()
    url = images_mod._resolve_lorcana(card, "The First Chapter")
    assert url == "https://cards.lorcast.io/card/x.avif"
    assert queries[0] == "Maui - Demigod set:1"
    assert "Maui set:1" in queries


def test_resolve_pokemon_requires_name_and_set(session, monkeypatch):
    session.add(Card(game_id=6, id_product=800, name="Weedle [Multiply]"))
    session.commit()

    queries = []

    def fake_http_get(url, params=None, source="", timeout=30.0):
        queries.append((params or {}).get("q"))
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": "Weedle",
                        "set": {"name": "Challenge from the Darkness"},
                        "images": {"large": "https://images.pokemontcg.io/g1/2.png"},
                    },
                    {
                        "name": "Weedle",
                        "set": {"name": "Gym Challenge"},
                        "images": {"large": "https://images.pokemontcg.io/g2/4.png"},
                    },
                    {
                        "name": "Kakuna",
                        "set": {"name": "Gym Challenge"},
                        "images": {"large": "https://images.pokemontcg.io/g2/5.png"},
                    },
                ]
            },
        )

    monkeypatch.setattr(images_mod, "_http_get", fake_http_get)

    card = session.query(Card).filter_by(id_product=800).one()
    assert (
        images_mod._resolve_pokemon(card, "Gym Challenge")
        == "https://images.pokemontcg.io/g2/4.png"
    )
    assert queries[0] == 'name:"Weedle"'
    assert images_mod._resolve_pokemon(card, "Set desconocida") is None


def test_img_endpoint_serves_placeholder(session):
    from fastapi.testclient import TestClient

    from app.main import app

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            resp = client.get("/img/123456")
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("image/svg+xml")
            assert resp.headers["cache-control"] == "no-store"
            assert resp.content == images_mod.PLACEHOLDER_SVG
    finally:
        app.dependency_overrides.clear()


def test_download_blocks_redirect_to_disallowed_host(session, monkeypatch):
    session.add(
        CardImage(
            id_product=303,
            game_id=1,
            source="scryfall",
            source_url="https://cards.scryfall.io/normal/a.jpg",
            fetched_at=utcnow(),
        )
    )
    session.commit()

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
        )

    _patch_client(monkeypatch, handler)

    content, content_type, cache = images_mod.get_image(session, 303)
    assert content_type == "image/svg+xml"
    assert cache == "no-store"
    assert len(calls) == 1
    row = session.query(CardImage).filter_by(id_product=303).one()
    assert row.failed_at is not None
    assert row.local_path is None


def test_serve_local_blocks_path_traversal(session, images_env):
    outside = images_env.parent / "secret.txt"
    outside.write_text("TOP-SECRET", encoding="utf-8")
    session.add(
        CardImage(
            id_product=404,
            game_id=1,
            source="scryfall",
            source_url=None,
            local_path="../secret.txt",
            fetched_at=utcnow(),
        )
    )
    session.add(
        CardImage(
            id_product=405,
            game_id=1,
            source="scryfall",
            source_url=None,
            local_path=str(outside),
            fetched_at=utcnow(),
        )
    )
    session.commit()

    for id_product in (404, 405):
        content, content_type, cache = images_mod.get_image(session, id_product)
        assert content_type == "image/svg+xml"
        assert cache == "no-store"
        assert b"TOP-SECRET" not in content
        row = session.query(CardImage).filter_by(id_product=id_product).one()
        assert row.local_path is None


def test_http_get_retries_transport_errors(monkeypatch):
    attempts = []

    class FlakyClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, params=None):
            attempts.append(url)
            if len(attempts) < 3:
                raise httpx.ConnectTimeout("boom")
            return httpx.Response(200, json={"ok": True})

    fake = types.SimpleNamespace(
        Client=FlakyClient,
        HTTPError=httpx.HTTPError,
        TransportError=httpx.TransportError,
    )
    monkeypatch.setattr(images_mod, "httpx", fake)
    monkeypatch.setattr(images_mod.time, "sleep", lambda seconds: None)

    resp = images_mod._http_get("https://example.com/x")
    assert resp.status_code == 200
    assert len(attempts) == 3

    class AlwaysFail(FlakyClient):
        def get(self, url, params=None):
            attempts.append(url)
            raise httpx.ConnectTimeout("boom")

    monkeypatch.setattr(
        images_mod,
        "httpx",
        types.SimpleNamespace(
            Client=AlwaysFail,
            HTTPError=httpx.HTTPError,
            TransportError=httpx.TransportError,
        ),
    )
    attempts.clear()
    with pytest.raises(httpx.ConnectTimeout):
        images_mod._http_get("https://example.com/y")
    assert len(attempts) == 3


def test_resolver_network_error_sets_negative_cache(session, monkeypatch):
    session.add(Card(game_id=6, id_product=900, name="Pikachu"))
    session.commit()

    def boom(url, params=None, source="", timeout=30.0):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(images_mod, "_http_get", boom)

    content, content_type, cache = images_mod.get_image(session, 900)
    assert content_type == "image/svg+xml"
    assert cache == "no-store"

    row = session.query(CardImage).filter_by(id_product=900).one()
    assert row.failed_at is not None
    assert row.source_url is None


def test_resolve_ygoprodeck_skips_wrong_name(session, monkeypatch):
    session.add(Card(game_id=3, id_product=910, name="Dark Magician"))
    session.commit()

    def fake(url, params=None, source="", timeout=30.0):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": "Dark Magician Girl",
                        "card_images": [
                            {"image_url": "https://images.ygoprodeck.com/images/cards/girl.jpg"}
                        ],
                    },
                    {
                        "name": "Dark Magician",
                        "card_images": [
                            {"image_url": "https://images.ygoprodeck.com/images/cards/dm.jpg"}
                        ],
                    },
                ]
            },
        )

    monkeypatch.setattr(images_mod, "_http_get", fake)

    card = session.query(Card).filter_by(id_product=910).one()
    assert (
        images_mod._resolve_ygoprodeck(card, None)
        == "https://images.ygoprodeck.com/images/cards/dm.jpg"
    )


def test_download_rejects_non_image_content_type(session, monkeypatch):
    session.add(
        CardImage(
            id_product=505,
            game_id=1,
            source="scryfall",
            source_url="https://cards.scryfall.io/normal/a.jpg",
            fetched_at=utcnow(),
        )
    )
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html>no soy imagen</html>", headers={"content-type": "text/html"}
        )

    _patch_client(monkeypatch, handler)

    content, content_type, cache = images_mod.get_image(session, 505)
    assert content_type == "image/svg+xml"
    assert cache == "no-store"
    row = session.query(CardImage).filter_by(id_product=505).one()
    assert row.failed_at is not None
    assert row.local_path is None


def test_sync_outside_sources_returns_zero(session):
    client = httpx.Client(
        transport=_mock_transport(lambda request: httpx.Response(200, json=[]))
    )
    assert images_mod.sync_game_images(client, session, 3) == 0
    assert images_mod.sync_game_images(client, session, 19) == 0


def test_save_file_leaves_no_temp():
    images_mod._save_file("101.jpg", IMAGE_BYTES)
    target = images_mod.IMAGES_DIR / "101.jpg"
    assert target.read_bytes() == IMAGE_BYTES
    assert list(images_mod.IMAGES_DIR.glob("*.part")) == []


def test_background_resolution_respects_slot_budget(monkeypatch):
    called = []
    monkeypatch.setattr(images_mod, "get_image", lambda db, i: called.append(i))
    acquired = 0
    try:
        for _ in range(images_mod._SLOT_COUNT):
            assert images_mod.try_acquire_slot()
            acquired += 1
        images_mod.resolve_in_background(55)
        assert called == []
    finally:
        for _ in range(acquired):
            images_mod.release_slot()

    images_mod.resolve_in_background(55)
    assert called == [55]


def test_pending_placeholder_is_distinguishable():
    final = images_mod.placeholder()
    pending = images_mod.placeholder(pending=True)
    assert final[0] == images_mod.PLACEHOLDER_SVG
    assert pending[0] != final[0]
    assert b'width="489"' in final[0]
    assert b'height="681"' in final[0]
    assert b'width="491"' in pending[0]
    assert b'height="683"' in pending[0]
    assert pending[1] == "image/svg+xml"
    assert pending[2] == "no-store"


def test_img_endpoint_returns_pending_when_slots_busy(session):
    from fastapi.testclient import TestClient

    from app.main import app

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    acquired = 0
    try:
        for _ in range(images_mod._SLOT_COUNT):
            assert images_mod.try_acquire_slot()
            acquired += 1
        with TestClient(app) as client:
            resp = client.get("/img/987654")
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("image/svg+xml")
            assert resp.headers["cache-control"] == "no-store"
            assert resp.content == images_mod.PLACEHOLDER_PENDING_SVG
            assert b'width="491"' in resp.content
            assert b'height="683"' in resp.content
    finally:
        for _ in range(acquired):
            images_mod.release_slot()
        app.dependency_overrides.clear()


def test_serve_local_rejects_unknown_content_type(session, images_env):
    (images_env / "707.jpg").write_bytes(IMAGE_BYTES)
    session.add(
        CardImage(
            id_product=707,
            game_id=1,
            source="scryfall",
            source_url=None,
            local_path="707.jpg",
            content_type="text/html",
            fetched_at=utcnow(),
        )
    )
    session.commit()

    content, content_type, cache = images_mod.get_image(session, 707)
    assert content_type == "image/svg+xml"
    assert content == images_mod.PLACEHOLDER_SVG
    row = session.query(CardImage).filter_by(id_product=707).one()
    assert row.local_path is None


def test_download_stops_after_too_many_redirects(session, monkeypatch):
    session.add(
        CardImage(
            id_product=808,
            game_id=1,
            source="scryfall",
            source_url="https://cards.scryfall.io/normal/a.jpg",
            fetched_at=utcnow(),
        )
    )
    session.commit()

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "/again.jpg"})

    _patch_client(monkeypatch, handler)

    content, content_type, cache = images_mod.get_image(session, 808)
    assert content_type == "image/svg+xml"
    assert cache == "no-store"
    assert len(calls) == 6
    row = session.query(CardImage).filter_by(id_product=808).one()
    assert row.failed_at is not None


def test_throttle_enforces_min_interval(monkeypatch):
    fake = types.SimpleNamespace(_t=0.0)
    fake.monotonic = lambda: fake._t

    def fake_sleep(seconds):
        fake._t += seconds

    fake.sleep = fake_sleep
    monkeypatch.setattr(images_mod, "time", fake)
    monkeypatch.setattr(images_mod, "MIN_INTERVAL", {"fuente": 0.15})
    monkeypatch.setattr(images_mod, "_LAST_CALL", {})
    images_mod._throttle("fuente")
    images_mod._throttle("fuente")
    assert fake._t >= 0.30


def test_img_endpoint_survives_locked_db():
    from fastapi.testclient import TestClient

    from app.main import app

    class LockedSession:
        def get(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("database is locked"))

        def rollback(self):
            pass

    def override_get_db():
        yield LockedSession()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            resp = client.get("/img/111222")
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("image/svg+xml")
            assert resp.headers["cache-control"] == "no-store"
            assert resp.content == images_mod.PLACEHOLDER_PENDING_SVG
    finally:
        app.dependency_overrides.clear()


def test_thumbs_js_matches_placeholder_contract():
    js = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "thumbs.js"
    ).read_text(encoding="utf-8")
    assert "naturalWidth === 491" in js
    assert "naturalHeight === 683" in js
    assert b'width="491"' in images_mod.PLACEHOLDER_PENDING_SVG
    assert b'height="683"' in images_mod.PLACEHOLDER_PENDING_SVG
