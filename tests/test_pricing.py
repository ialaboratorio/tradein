from decimal import Decimal

from app.services.pricing import list_totals, money, unit_cash, unit_store
from app.models import TradeLine


def test_coefficients():
    assert unit_cash(10.0) == Decimal("7.00")
    assert unit_store(10.0) == Decimal("8.05")
    assert unit_cash(0.333) == Decimal("0.23")
    assert unit_cash(None) is None


def test_low_under_five_cents_offers_zero():
    assert unit_cash(0.0) == Decimal("0.00")
    assert unit_store(0.0) == Decimal("0.00")
    assert unit_cash(0.049) == Decimal("0.00")
    assert unit_store(0.049) == Decimal("0.00")
    assert unit_cash(0.05) == Decimal("0.04")
    assert unit_store(0.05) == Decimal("0.04")


def test_money_rounding():
    assert money(Decimal("2.675")) == Decimal("2.68")
    assert money(Decimal("2.674")) == Decimal("2.67")


def test_list_totals_with_override():
    line1 = TradeLine(
        list_id=1,
        game_id=1,
        id_product=1,
        card_name="A",
        quantity=2,
        foil=False,
        condition="nm",
        unit_low=10.0,
    )
    line2 = TradeLine(
        list_id=1,
        game_id=1,
        id_product=2,
        card_name="B",
        quantity=1,
        foil=False,
        condition="nm",
        unit_low=None,
        unit_final=5.0,
    )
    totals = list_totals([line1, line2])
    assert totals["total_cash"] == Decimal("19.00")
    assert totals["missing_price"] == 0
    store_equivalent = unit_store(10.0) * 2 + Decimal("5.75")
    assert totals["total_store"] == store_equivalent
