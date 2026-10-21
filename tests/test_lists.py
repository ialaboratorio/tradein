import base64
import json

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Card, CardPrice, User

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
    resp = client.post("/listas", data={"note": "entrega"}, follow_redirects=False)
    assert resp.status_code == 302
    list_url = resp.headers["location"]
    list_id = list_url.rstrip("/").split("/")[-1]

    resp = client.post(
        f"/listas/{list_id}/lineas",
        data={"game_id": 1, "id_product": 101, "quantity": 2, "condition": "nm"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    page = client.get(f"/listas/{list_id}")
    assert page.status_code == 200
    assert "Lightning Bolt" in page.text
    assert "10.00" in page.text
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
        data={"game_id": 1, "id_product": 101, "quantity": 1},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/listas"
    assert client.get("/listas").status_code == 200


def test_refresh_prices_alert(client, session):
    resp = client.post("/listas", data={"note": ""}, follow_redirects=False)
    list_id = resp.headers["location"].rstrip("/").split("/")[-1]
    client.post(
        f"/listas/{list_id}/lineas",
        data={"game_id": 1, "id_product": 101, "quantity": 1},
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
