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
from app.models import Card, CardPrice, TradeLine, TradeList, User

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
    s.add(User(sub="admin1", email="admin@example.com", name="Admin", is_admin=True))
    s.add(User(sub="u1", email="user@example.com", is_admin=False))
    s.add(Card(game_id=1, id_product=101, name="Lightning Bolt"))
    s.add(CardPrice(game_id=1, id_product=101, low=10.0))
    s.commit()
    trade_list = TradeList(user_sub="u1", status="enviada", note="entrega")
    s.add(trade_list)
    s.flush()
    s.add(
        TradeLine(
            list_id=trade_list.id,
            game_id=1,
            id_product=101,
            card_name="Lightning Bolt",
            quantity=2,
            foil=False,
            condition="nm",
            unit_low=10.0,
        )
    )
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
        yield c
    app.dependency_overrides.clear()


def _as(client, sub):
    client.cookies.set("session", session_cookie({"user_sub": sub}))


def test_non_admin_redirected(client):
    _as(client, "u1")
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


def test_dashboard_shows_pending(client):
    _as(client, "admin1")
    page = client.get("/admin")
    assert page.status_code == 200
    assert "Pendientes de validar" in page.text
    assert "user@example.com" in page.text


def test_validate_adjust_pay_flow(client, session):
    trade_list = session.query(TradeList).one()
    line = session.query(TradeLine).one()
    _as(client, "admin1")

    resp = client.post(f"/admin/listas/{trade_list.id}/validar", follow_redirects=False)
    assert resp.status_code == 302
    assert session.get(TradeList, trade_list.id).status == "validada"

    resp = client.post(
        f"/admin/lineas/{line.id}/ajustar",
        data={"unit_final": "3,50", "reason": "borde rayado"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    refreshed = session.get(TradeLine, line.id)
    assert refreshed.unit_final == 3.5
    assert refreshed.override_reason == "borde rayado"

    page = client.get(f"/admin/listas/{trade_list.id}")
    assert "3.50" in page.text
    assert "borde rayado" in page.text

    resp = client.post(
        f"/admin/listas/{trade_list.id}/pagar",
        data={"payment_method": "cash"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    final = session.get(TradeList, trade_list.id)
    assert final.status == "pagada"
    assert final.payment_method == "cash"

    page = client.get(f"/admin/listas/{trade_list.id}")
    assert "Efectivo" in page.text


def test_adjust_clear(client, session):
    trade_list = session.query(TradeList).one()
    line = session.query(TradeLine).one()
    line.unit_final = 1.0
    line.override_reason = "old"
    session.commit()
    _as(client, "admin1")

    client.post(f"/admin/lineas/{line.id}/ajustar", data={"unit_final": "", "reason": ""})
    refreshed = session.get(TradeLine, line.id)
    assert refreshed.unit_final is None
    assert refreshed.override_reason is None


def test_reject_with_reason(client, session):
    trade_list = session.query(TradeList).one()
    _as(client, "admin1")
    client.post(
        f"/admin/listas/{trade_list.id}/rechazar",
        data={"reason": "cartas en mal estado"},
    )
    assert session.get(TradeList, trade_list.id).status == "rechazada"


def test_adjust_rejects_nan_and_infinity(client, session):
    trade_list = session.query(TradeList).one()
    line = session.query(TradeLine).one()
    _as(client, "admin1")
    for bad in ("NaN", "Infinity", "-Infinity", "-1", "1e30", "1E+100", "1_0", "abc"):
        resp = client.post(
            f"/admin/lineas/{line.id}/ajustar",
            data={"unit_final": bad, "reason": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "error=ajuste" in resp.headers["location"]
    assert session.get(TradeLine, line.id).unit_final is None


def test_adjust_blocked_when_pagada(client, session):
    trade_list = session.query(TradeList).one()
    line = session.query(TradeLine).one()
    trade_list.status = "pagada"
    session.commit()
    _as(client, "admin1")
    resp = client.post(
        f"/admin/lineas/{line.id}/ajustar",
        data={"unit_final": "1.00", "reason": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert session.get(TradeLine, line.id).unit_final is None
