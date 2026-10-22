import base64
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Card, CardPrice, User
from app.services import pricing

SESSION_SECRET = "test-secret-key"


def session_cookie(data: dict) -> str:
    payload = base64.b64encode(json.dumps(data).encode("utf-8"))
    signer = TimestampSigner(SESSION_SECRET)
    return signer.sign(payload).decode("utf-8")


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
    s.add(User(sub="u1", email="user@example.com", name="User", is_admin=False))
    s.add(Card(game_id=1, id_product=101, name="Lightning Bolt", id_expansion=209))
    s.add(CardPrice(game_id=1, id_product=101, low=10.0, low_foil=20.0))
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


@pytest.fixture()
def client(session):
    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        c.cookies.set("session", session_cookie({"user_sub": "u1"}))
        yield c
    app.dependency_overrides.clear()


def test_full_draft_flow(client):
    resp = client.post(
        "/listas", data={"note": "entrega", "game_id": 1}, follow_redirects=False
    )
    assert resp.status_code == 302
    list_url = resp.headers["location"]
    list_id = list_url.rstrip("/").split("/")[-1]

    resp = client.post(
        f"/listas/{list_id}/lineas",
        data={"id_product": 101, "quantity": 2, "language": "es"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    page = client.get(f"/listas/{list_id}")
    assert page.status_code == 200
    assert "versions-modal" in page.text
    assert "Lightning Bolt" in page.text
    assert "Español" in page.text
    assert "LOW (mercado)" not in page.text
    assert "10.00" not in page.text
    assert "7.00" in page.text
    assert "8.05" in page.text
    assert "14.00" in page.text
    assert "16.10" in page.text

    resp = client.post(f"/listas/{list_id}/enviar", follow_redirects=False)
    assert resp.status_code == 302

    page = client.get(f"/listas/{list_id}")
    assert "badge-enviada" in page.text

    resp = client.post(
        f"/listas/{list_id}/lineas",
        data={"id_product": 101, "quantity": 1},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/listas"
    assert client.get("/listas").status_code == 200


def test_refresh_prices_alert(client, session):
    resp = client.post(
        "/listas", data={"note": "", "game_id": 1}, follow_redirects=False
    )
    list_id = resp.headers["location"].rstrip("/").split("/")[-1]
    client.post(
        f"/listas/{list_id}/lineas",
        data={"id_product": 101, "quantity": 1},
    )

    price = session.get(CardPrice, (1, 101))
    price.low = 20.0
    session.commit()

    client.post(f"/listas/{list_id}/refrescar")
    page = client.get(f"/listas/{list_id}")
    assert "revisar" in page.text


def test_cannot_touch_others_list(client, session):
    from app.models import TradeList

    other = TradeList(user_sub="someone-else", status="borrador")
    session.add(other)
    session.commit()

    resp = client.post(f"/listas/{other.id}/enviar", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/listas"


def test_list_is_mono_game(client, session):
    session.add(Card(game_id=22, id_product=900, name="Rift Card", id_expansion=1))
    session.add(CardPrice(game_id=22, id_product=900, low=5.0))
    session.commit()

    resp = client.post(
        "/listas", data={"note": "", "game_id": 1}, follow_redirects=False
    )
    list_id = resp.headers["location"].rstrip("/").split("/")[-1]

    resp = client.post(
        f"/listas/{list_id}/lineas",
        data={"id_product": 900, "quantity": 1},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "error=sin_carta" in resp.headers["location"]


def test_forces_nm_and_saves_language(client, session):
    resp = client.post(
        "/listas", data={"note": "", "game_id": 1}, follow_redirects=False
    )
    list_id = resp.headers["location"].rstrip("/").split("/")[-1]

    client.post(
        f"/listas/{list_id}/lineas",
        data={"id_product": 101, "quantity": 1, "language": "en", "condition": "played"},
    )

    from app.models import TradeLine

    line = session.query(TradeLine).one()
    assert line.condition == "nm"
    assert line.language == "en"
    page = client.get(f"/listas/{list_id}")
    assert "Inglés" in page.text


def test_create_list_only_supported_games(client):
    resp = client.post("/listas", data={"game_id": 3}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/listas"

    resp = client.post("/listas", data={"game_id": 24}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/listas/")
    assert resp.headers["location"] != "/listas"


def test_low_under_five_cents_offers_zero(client, session):
    session.add(Card(game_id=1, id_product=55, name="Bulk Common", id_expansion=1))
    session.add(CardPrice(game_id=1, id_product=55, low=0.03))
    session.commit()

    resp = client.post(
        "/listas", data={"note": "", "game_id": 1}, follow_redirects=False
    )
    list_id = resp.headers["location"].rstrip("/").split("/")[-1]
    client.post(f"/listas/{list_id}/lineas", data={"id_product": 55, "quantity": 4})

    page = client.get(f"/listas/{list_id}")
    assert "Bulk Common" in page.text
    assert "0.00 €" in page.text
    assert "0.03" not in page.text

    from app.models import TradeList

    trade_list = session.get(TradeList, int(list_id))
    totals = pricing.list_totals(list(trade_list.lines))
    assert totals["total_cash"] == Decimal("0.00")
    assert totals["total_store"] == Decimal("0.00")
