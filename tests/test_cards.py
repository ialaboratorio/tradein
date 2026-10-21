import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Card, CardPrice
from app.services.cardmarket import sync_game_catalog, sync_game_prices

PRODUCTS_FIXTURE = {
    "version": 1,
    "createdAt": "2026-10-21T02:45:00+0200",
    "products": [
        {
            "idProduct": 101,
            "name": "Lightning Bolt",
            "idCategory": 1,
            "idExpansion": 209,
            "categoryName": "Magic Single",
        },
        {"idProduct": 102, "name": "Sol Ring", "idCategory": 1, "idExpansion": 210},
    ],
}

PRICES_FIXTURE = {
    "version": 1,
    "createdAt": "2026-10-21T02:45:00+0200",
    "priceGuides": [
        {"idProduct": 101, "low": 0.45, "low-foil": 1.2, "trend": 0.5, "avg7": 0.48},
        {"idProduct": 102, "low": 3.1, "low-foil": None, "trend": 3.0, "avg7": 3.05},
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


def _mock_client(url_payloads: dict[str, dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = url_payloads[request.url.path]
        return httpx.Response(200, content=json.dumps(payload).encode())

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sync_catalog_and_prices_idempotent(session):
    from app.services.cardmarket import PRICE_GUIDE_URL, PRODUCT_LIST_URL

    urls = {
        PRODUCT_LIST_URL.format(game=1).replace("https://downloads.s3.cardmarket.com", ""): PRODUCTS_FIXTURE,
        PRICE_GUIDE_URL.format(game=1).replace("https://downloads.s3.cardmarket.com", ""): PRICES_FIXTURE,
    }
    with _mock_client(urls) as client:
        added = sync_game_catalog(client, session, 1)
        assert added == 2
        added_again = sync_game_catalog(client, session, 1)
        assert added_again == 0

        updated = sync_game_prices(client, session, 1)
        assert updated == 2
        updated_again = sync_game_prices(client, session, 1)
        assert updated_again == 2

    card = session.query(Card).filter_by(id_product=101).one()
    assert card.name == "Lightning Bolt"
    assert card.category_name == "Magic Single"
    assert session.query(Card).filter_by(id_product=102).one().category_name is None
    price = session.query(CardPrice).filter_by(id_product=101).one()
    assert price.low == 0.45
    assert price.low_foil == 1.2
    assert price.source == "cardmarket_s3"


def test_search_cards_endpoint(session):
    from app.models import Expansion

    session.add(Card(game_id=1, id_product=101, name="Lightning Bolt", id_expansion=209))
    session.add(CardPrice(game_id=1, id_product=101, low=0.45, low_foil=1.2))
    session.add(Card(game_id=1, id_product=102, name="Sol Ring", id_expansion=210))
    session.add(Expansion(game_id=1, id_expansion=209, name="Core Set 2021"))
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            resp = client.get("/api/cards/search", params={"q": "light"})
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["name"] == "Lightning Bolt"
            assert data[0]["low"] == 0.45
            assert data[0]["game_name"] == "Magic"
            assert data[0]["has_price"] is True
            assert data[0]["expansion_name"] == "Core Set 2021"
            assert data[0]["set_label"] == "Core Set 2021"

            resp_no_price = client.get("/api/cards/search", params={"q": "sol"})
            no_price = resp_no_price.json()[0]
            assert no_price["has_price"] is False
            assert no_price["expansion_name"] is None
            assert no_price["set_label"] == "Set #210"
    finally:
        app.dependency_overrides.clear()
