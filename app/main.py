import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routers import admin, auth, cards, lists
from app.security import get_optional_user
from app.services.sync import initial_sync_if_stale, scheduler_loop

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.validate_runtime()
    tasks: list[asyncio.Task] = []
    if settings.enable_scheduler:
        tasks.append(asyncio.create_task(scheduler_loop()))
        tasks.append(asyncio.create_task(initial_sync_if_stale()))
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.state.templates = templates

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=settings.session_https_only,
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

app.include_router(auth.router)
app.include_router(cards.router)
app.include_router(lists.router)
app.include_router(admin.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index(request: Request, user=Depends(get_optional_user)):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"request": request, "user": user},
    )
