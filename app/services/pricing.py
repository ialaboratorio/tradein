from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.config import settings
from app.models import TradeLine

MIN_OFFER_LOW = Decimal("0.05")


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _offered(low: float) -> Decimal:
    if Decimal(str(low)) < MIN_OFFER_LOW:
        return Decimal("0")
    return Decimal(str(low))


def unit_cash(low: float | None) -> Decimal | None:
    if low is None:
        return None
    return money(_offered(low) * Decimal(str(settings.coef_cash)))


def unit_store(low: float | None) -> Decimal | None:
    if low is None:
        return None
    return money(_offered(low) * Decimal(str(settings.coef_store)))


def line_amounts(line: TradeLine) -> dict:
    if line.unit_final is not None:
        base = money(Decimal(str(line.unit_final)))
        cash = base
        store = money(base * Decimal(str(settings.coef_store)) / Decimal(str(settings.coef_cash)))
    else:
        cash = unit_cash(line.unit_low)
        store = unit_store(line.unit_low)
    qty = Decimal(line.quantity)
    return {
        "unit_low": line.unit_low,
        "unit_cash": cash,
        "unit_store": store,
        "total_cash": cash * qty if cash is not None else None,
        "total_store": store * qty if store is not None else None,
    }


def list_totals(lines: list[TradeLine]) -> dict:
    total_cash = Decimal("0")
    total_store = Decimal("0")
    missing = 0
    for line in lines:
        amounts = line_amounts(line)
        if amounts["total_cash"] is None or amounts["total_store"] is None:
            missing += 1
            continue
        total_cash += amounts["total_cash"]
        total_store += amounts["total_store"]
    return {
        "total_cash": money(total_cash),
        "total_store": money(total_store),
        "missing_price": missing,
    }


def price_changed_pct(old: float | None, new: float | None) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return abs(new - old) / old * 100


def refresh_line_price(line: TradeLine, new_low: float | None, fetched_at: datetime | None) -> bool:
    pct = price_changed_pct(line.unit_low, new_low)
    line.price_alert = bool(
        pct is not None and pct > settings.price_alert_pct and line.unit_final is None
    )
    line.unit_low = new_low
    line.price_fetched_at = fetched_at
    return line.price_alert
