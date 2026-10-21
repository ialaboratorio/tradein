from urllib.parse import urlparse

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def _redirect_uri(request: Request) -> str:
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/") + "/auth/google/callback"
    return str(request.url_for("google_callback"))


def _safe_next(next_url: str | None) -> str:
    if not next_url:
        return "/"
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc or not next_url.startswith("/") or "\\" in next_url:
        return "/"
    return next_url


@router.get("/google/login")
async def google_login(request: Request):
    if not settings.google_client_id:
        return RedirectResponse("/?error=google_no_configurado", status_code=302)
    request.session["next"] = _safe_next(request.query_params.get("next"))
    redirect_uri = _redirect_uri(request)
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo") or {}
    if not userinfo:
        resp = await oauth.google.get("userinfo", token=token)
        userinfo = resp.json()

    sub = userinfo.get("sub")
    email = (userinfo.get("email") or "").lower()
    if not sub or not email:
        return RedirectResponse("/?error=google_sin_email", status_code=302)

    user = db.get(User, sub)
    if user is None:
        user = User(sub=sub, email=email)
        db.add(user)
    user.email = email
    user.name = userinfo.get("name")
    user.picture = userinfo.get("picture")
    user.is_admin = email in settings.admin_email_set()
    db.commit()

    request.session["user_sub"] = sub
    next_url = _safe_next(request.session.pop("next", None))
    return RedirectResponse(next_url, status_code=302)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)
