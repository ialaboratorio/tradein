from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import GAME_NAMES, ListEvent, SyncRun, TradeLine, TradeList, User, utcnow
from app.routers.lists import enrich_lines
from app.security import get_optional_user
from app.services import pricing

router = APIRouter(prefix="/admin", tags=["admin"])

PAYMENT_LABELS = {"cash": "Efectivo", "store": "Dinero de tienda"}


def _guard_admin(request: Request, user) -> RedirectResponse | None:
    if user is None:
        return RedirectResponse(f"/auth/google/login?next={request.url.path}", status_code=302)
    if not user.is_admin:
        return RedirectResponse("/", status_code=302)
    return None


def _admin_list(db: Session, list_id: int) -> TradeList | None:
    return db.get(
        TradeList,
        list_id,
        options=(
            joinedload(TradeList.lines),
            joinedload(TradeList.events),
        ),
    )


@router.get("")
def dashboard(request: Request, user=Depends(get_optional_user), db: Session = Depends(get_db)):
    redirect = _guard_admin(request, user)
    if redirect:
        return redirect
    lists = db.scalars(
        select(TradeList).options(joinedload(TradeList.owner)).order_by(TradeList.updated_at.desc())
    ).unique().all()
    pending = [trade_list for trade_list in lists if trade_list.status == "enviada"]
    sync_runs = db.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(12)).all()
    return request.app.state.templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "request": request,
            "user": user,
            "lists": lists,
            "pending": pending,
            "sync_runs": sync_runs,
            "game_names": GAME_NAMES,
            "payment_labels": PAYMENT_LABELS,
        },
    )


@router.get("/listas/{list_id}")
def admin_list_detail(
    request: Request,
    list_id: int,
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    redirect = _guard_admin(request, user)
    if redirect:
        return redirect
    trade_list = _admin_list(db, list_id)
    if trade_list is None:
        return RedirectResponse("/admin", status_code=302)
    lines = sorted(trade_list.lines, key=lambda line: line.card_name.lower())
    totals = pricing.list_totals(lines)
    owner = db.get(User, trade_list.user_sub)
    return request.app.state.templates.TemplateResponse(
        request,
        "admin_lista.html",
        {
            "request": request,
            "user": user,
            "trade_list": trade_list,
            "owner": owner,
            "rows": enrich_lines(db, lines),
            "totals": totals,
            "events": sorted(trade_list.events, key=lambda event: event.id, reverse=True),
            "payment_labels": PAYMENT_LABELS,
            "game_names": GAME_NAMES,
        },
    )


@router.post("/listas/{list_id}/validar")
def validate_list(
    request: Request,
    list_id: int,
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    redirect = _guard_admin(request, user)
    if redirect:
        return redirect
    trade_list = db.get(TradeList, list_id)
    if trade_list is None or trade_list.status != "enviada":
        return RedirectResponse("/admin", status_code=302)
    trade_list.status = "validada"
    trade_list.updated_at = utcnow()
    db.add(ListEvent(list_id=trade_list.id, actor_sub=user.sub, action="validada"))
    db.commit()
    return RedirectResponse(f"/admin/listas/{list_id}", status_code=302)


@router.post("/listas/{list_id}/rechazar")
def reject_list(
    request: Request,
    list_id: int,
    reason: str = Form(""),
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    redirect = _guard_admin(request, user)
    if redirect:
        return redirect
    trade_list = db.get(TradeList, list_id)
    if trade_list is None or trade_list.status != "enviada":
        return RedirectResponse("/admin", status_code=302)
    trade_list.status = "rechazada"
    trade_list.updated_at = utcnow()
    db.add(
        ListEvent(
            list_id=trade_list.id,
            actor_sub=user.sub,
            action="rechazada",
            detail=reason.strip()[:500] or None,
        )
    )
    db.commit()
    return RedirectResponse(f"/admin/listas/{list_id}", status_code=302)


@router.post("/lineas/{line_id}/ajustar")
def adjust_line(
    request: Request,
    line_id: int,
    unit_final: str = Form(""),
    reason: str = Form(""),
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    redirect = _guard_admin(request, user)
    if redirect:
        return redirect
    line = db.get(TradeLine, line_id)
    if line is None:
        return RedirectResponse("/admin", status_code=302)
    trade_list = line.trade_list
    if trade_list.status not in ("enviada", "validada"):
        return RedirectResponse(f"/admin/listas/{trade_list.id}", status_code=302)
    raw = unit_final.strip().replace(",", ".")
    if raw == "":
        line.unit_final = None
        line.override_reason = None
        detail = f"{line.card_name}: ajuste retirado"
    else:
        if not raw.replace(".", "", 1).isdigit():
            return RedirectResponse(f"/admin/listas/{trade_list.id}?error=ajuste", status_code=302)
        try:
            value = Decimal(raw)
            if not value.is_finite() or value < 0 or value > Decimal("1000000"):
                raise InvalidOperation
            line.unit_final = float(pricing.money(value))
        except InvalidOperation:
            return RedirectResponse(f"/admin/listas/{trade_list.id}?error=ajuste", status_code=302)
        line.override_reason = reason.strip()[:255] or None
        detail = f"{line.card_name}: ajuste a {line.unit_final:.2f} EUR"
    trade_list.updated_at = utcnow()
    db.add(ListEvent(list_id=trade_list.id, actor_sub=user.sub, action="linea_ajustada", detail=detail))
    db.commit()
    return RedirectResponse(f"/admin/listas/{trade_list.id}", status_code=302)


@router.post("/listas/{list_id}/pagar")
def pay_list(
    request: Request,
    list_id: int,
    payment_method: str = Form(...),
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    redirect = _guard_admin(request, user)
    if redirect:
        return redirect
    trade_list = db.get(TradeList, list_id)
    if trade_list is None or trade_list.status != "validada":
        return RedirectResponse("/admin", status_code=302)
    if payment_method not in PAYMENT_LABELS:
        payment_method = "cash"
    trade_list.status = "pagada"
    trade_list.payment_method = payment_method
    trade_list.updated_at = utcnow()
    db.add(
        ListEvent(
            list_id=trade_list.id,
            actor_sub=user.sub,
            action="pagada",
            detail=PAYMENT_LABELS[payment_method],
        )
    )
    db.commit()
    return RedirectResponse(f"/admin/listas/{list_id}", status_code=302)
