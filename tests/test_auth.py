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
from app.models import User

SESSION_SECRET = "test-secret-key"


def session_cookie(data: dict) -> str:
    payload = base64.b64encode(json.dumps(data).encode("utf-8"))
    signer = TimestampSigner(SESSION_SECRET)
    return signer.sign(payload).decode("utf-8")


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_login_redirects_to_google(client):
    response = client.get("/auth/google/login", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "accounts.google.com" in response.headers["location"]


def test_logout(client):
    response = client.post("/auth/logout", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/"


def test_index_logged_out(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Entrar con Google" in response.text


def test_index_logged_in(db_session, client):
    db_session.add(User(sub="u1", email="user@example.com", name="User", is_admin=False))
    db_session.commit()
    client.cookies.set("session", session_cookie({"user_sub": "u1"}))
    response = client.get("/")
    assert response.status_code == 200
    assert "User" in response.text


def test_admin_link_only_for_admin(db_session, client):
    db_session.add(User(sub="u2", email="user@example.com", is_admin=False))
    db_session.add(User(sub="u3", email="admin@example.com", is_admin=True))
    db_session.commit()

    client.cookies.set("session", session_cookie({"user_sub": "u2"}))
    assert "/admin" not in client.get("/").text

    client.cookies.set("session", session_cookie({"user_sub": "u3"}))
    assert "/admin" in client.get("/").text
