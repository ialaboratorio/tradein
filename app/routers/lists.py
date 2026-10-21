from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import (
    GAME_NAMES,
    LANGUAGES,
    Card,
    CardPrice,
    ListEvent,
    TradeLine,
    TradeList,
    utcnow,
)
from app.security import get_optional_user
from app.services import pricing

router = APIRouter(tags=["lists"])


def _guard_login(request: Request, user) -> RedirectResponse | None:
    if user is None:
        return RedirectResponse(f"/auth/google/login?next={request.url.path}", status_code=302)
    return None


def _own_list(db: Session, user, list_id: int) -> TradeList | None:
    trade_list = db.get(TradeList, list_id, options=(joinedload(TradeList.lines),))
    if trade_list is None:
        return None
    if trade_list.user_sub != user.sub:
        return None
    return trade_list


def _touch(trade_list: TradeList) -> None:
    trade_list.updated_at = utcnow()


def enrich_lines(lines: list[TradeLine]) -> list[dict]:
    enriched = []
    for line in lines:
        amounts = pricing.line_amounts(line)
        enriched.append(
            {
                "line": line,
                "condition_label": "NM",
                "language_label": LANGUAGES.get(line.language, line.language),
                **amounts,
            }
        )
    return enriched


@router.get("/listas")
def list_lists(request: Request, user=Depends(get_optional_user), db: Session = Depends(get_db)):
    redirect = _guard_login(request, user)
    if redirect:
        return redirect
    lists = db.scalars(
        select(TradeList)
        .where(TradeList.user_sub == user.sub)
        .order_by(TradeList.created_at.desc())
    ).all()
    return request.app.state.templates.TemplateResponse(
        request,
        "listas.html",
        {"request": request, "user": user, "lists": lists, "game_names": GAME_NAMES},
    )


@router.post("/listas")
def create_list(
    request: Request,
    game_id: int = Form(1),
    note: str = Form(""),
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    redirect = _guard_login(request, user)
    if redirect:
        return redirect
    if game_id not in GAME_NAMES:
        return RedirectResponse("/listas", status_code=302)
    trade_list = TradeList(user_sub=user.sub, game_id=game_id, note=note.strip()[:200] or None)
    db.add(trade_list)
    db.commit()
    db.add(ListEvent(list_id=trade_list.id, actor_sub=user.sub, action="creada"))
    db.commit()
    return RedirectResponse(f"/listas/{trade_list.id}", status_code=302)


@router.get("/listas/{list_id}")
def list_detail(
    request: Request,
    list_id: int,
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    redirect = _guard_login(request, user)
    if redirect:
        return redirect
    trade_list = _own_list(db, user, list_id)
    if trade_list is None:
        return RedirectResponse("/listas", status_code=302)
    lines = sorted(trade_list.lines, key=lambda line: line.card_name.lower())
    totals = pricing.list_totals(lines)
    return request.app.state.templates.TemplateResponse(
        request,
        "lista_detalle.html",
        {
            "request": request,
            "user": user,
            "trade_list": trade_list,
            "rows": enrich_lines(lines),
            "totals": totals,
            "game_names": GAME_NAMES,
        },
    )


@router.post("/listas/{list_id}/lineas")
def add_line(
    request: Request,
    list_id: int,
    id_product: int = Form(...),
    quantity: int = Form(1, ge=1, le=999),
    foil: str = Form(""),
    language: str = Form("es"),
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    redirect = _guard_login(request, user)
    if redirect:
        return redirect
    trade_list = _own_list(db, user, list_id)
    if trade_list is None or trade_list.status != "borrador":
        return RedirectResponse("/listas", status_code=302)
    if language not in LANGUAGES:
        language = "es"

    game_id = trade_list.game_id
    card = db.get(Card, (game_id, id_product))
    if card is None:
        return RedirectResponse(f"/listas/{list_id}?error=sin_carta", status_code=302)

    price = db.get(CardPrice, (game_id, id_product))
    is_foil = foil == "on"
    unit_low = None
    fetched_at = None
    if price is not None:
        unit_low = price.low_foil if is_foil else price.low
        fetched_at = price.fetched_at

    db.add(
        TradeLine(
            list_id=trade_list.id,
            game_id=game_id,
            id_product=id_product,
            card_name=card.name,
            id_expansion=card.id_expansion,
            quantity=quantity,
            foil=is_foil,
            condition="nm",
            language=language,
            unit_low=unit_low,
            price_fetched_at=fetched_at,
        )
    )
    _touch(trade_list)
    db.add(
        ListEvent(
            list_id=trade_list.id,
            actor_sub=user.sub,
            action="linea_anadida",
            detail=f"{card.name} x{quantity}",
        )
    )
    db.commit()
    return RedirectResponse(f"/listas/{list_id}", status_code=302)


@router.post("/listas/{list_id}/lineas/{line_id}/eliminar")
def delete_line(
    request: Request,
    list_id: int,
    line_id: int,
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    redirect = _guard_login(request, user)
    if redirect:
        return redirect
    trade_list = _own_list(db, user, list_id)
    if trade_list is None or trade_list.status != "borrador":
        return RedirectResponse("/listas", status_code=302)
    line = db.get(TradeLine, line_id)
    if line and line.list_id == trade_list.id:
        detail = f"{line.card_name} x{line.quantity}"
        db.delete(line)
        _touch(trade_list)
        db.add(ListEvent(list_id=trade_list.id, actor_sub=user.sub, action="linea_eliminada", detail=detail))
        db.commit()
    return RedirectResponse(f"/listas/{list_id}", status_code=302)


@router.post("/listas/{list_id}/enviar")
def submit_list(
    request: Request,
    list_id: int,
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    redirect = _guard_login(request, user)
    if redirect:
        return redirect
    trade_list = _own_list(db, user, list_id)
    if trade_list is None or trade_list.status != "borrador":
        return RedirectResponse("/listas", status_code=302)
    if not trade_list.lines:
        return RedirectResponse(f"/listas/{list_id}?error=vacia", status_code=302)
    trade_list.status = "enviada"
    _touch(trade_list)
    db.add(ListEvent(list_id=trade_list.id, actor_sub=user.sub, action="enviada"))
    db.commit()
    return RedirectResponse(f"/listas/{list_id}", status_code=302)


@router.post("/listas/{list_id}/refrescar")
def refresh_prices(
    request: Request,
    list_id: int,
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    redirect = _guard_login(request, user)
    if redirect:
        return redirect
    trade_list = _own_list(db, user, list_id)
    if trade_list is None or trade_list.status not in ("borrador", "enviada", "validada"):
        return RedirectResponse("/listas", status_code=302)
    alerts = 0
    for line in trade_list.lines:
        price = db.get(CardPrice, (line.game_id, line.id_product))
        if price is None:
            continue
        new_low = price.low_foil if line.foil else price.low
        if new_low is None:
            continue
        if pricing.refresh_line_price(line, new_low, price.fetched_at):
            alerts += 1
    _touch(trade_list)
    db.add(
        ListEvent(
            list_id=trade_list.id,
            actor_sub=user.sub,
            action="precios_refrescados",
            detail=f"{alerts} lineas marcadas para revision",
        )
    )
    db.commit()
    return RedirectResponse(f"/listas/{list_id}", status_code=302)


@router.post("/listas/{list_id}/borrar")
def delete_list(
    request: Request,
    list_id: int,
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    redirect = _guard_login(request, user)
    if redirect:
        return redirect
    trade_list = _own_list(db, user, list_id)
    if trade_list is None or trade_list.status != "borrador":
        return RedirectResponse("/listas", status_code=302)
    db.delete(trade_list)
    db.commit()
    return RedirectResponse("/listas", status_code=302)
