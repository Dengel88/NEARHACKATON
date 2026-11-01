from decimal import Decimal

import pytest

from app.domain import (
    DomainError,
    InvoiceStatus,
    build_invoice_lines,
    build_quote_request,
    can_transition,
    from_units,
    map_oneclick_status,
    to_units,
)


def test_prices_come_from_catalog_not_model(settings):
    lines, total = build_invoice_lines(settings, [{"sku": "assistant-pro", "qty": 2, "price_usd": "0.01"}])
    assert total == Decimal("780.00")
    assert lines[0]["unit_price_usd"] == "390.00"


def test_duplicate_lines_are_merged(settings):
    lines, total = build_invoice_lines(settings, [{"sku": "landing", "qty": 1}, {"sku": "landing", "qty": 2}])
    assert len(lines) == 1 and lines[0]["qty"] == 3 and total == Decimal("750.00")


@pytest.mark.parametrize(
    "lines",
    [[], [{"sku": "nope", "qty": 1}], [{"sku": "landing", "qty": 0}], [{"sku": "landing", "qty": 101}],
     [{"sku": "landing", "qty": "two"}], [{"sku": "landing", "qty": 60}, {"sku": "landing", "qty": 60}]],
)
def test_bad_lines_rejected(settings, lines):
    with pytest.raises(DomainError):
        build_invoice_lines(settings, lines)


def test_units_roundtrip():
    assert to_units(Decimal("149.00"), 6) == 149_000_000
    assert to_units(Decimal("0.0000005"), 6) == 1
    assert from_units("1500000", 6) == Decimal("1.5")


def test_quote_request_is_exact_output_to_merchant(settings):
    req = build_quote_request(settings, origin_asset="nep141:wrap.near", refund_to="alice.near",
                              amount_units=1_000_000, deadline_iso="2026-11-14T00:00:00Z")
    assert req["swapType"] == "EXACT_OUTPUT"
    assert req["destinationAsset"] == settings.usdc_asset_id
    assert req["recipient"] == settings.merchant_near_account
    assert req["amount"] == "1000000" and req["dry"] is False


@pytest.mark.parametrize("kw", [{"origin_asset": ""}, {"refund_to": ""}, {"amount_units": 0}])
def test_quote_request_validation(settings, kw):
    args = dict(origin_asset="a", refund_to="alice.near", amount_units=1, deadline_iso="x") | kw
    with pytest.raises(DomainError):
        build_quote_request(settings, **args)


def test_status_mapping_and_finality():
    assert map_oneclick_status("SUCCESS") == InvoiceStatus.PAID
    assert map_oneclick_status("KNOWN_DEPOSIT_TX") == InvoiceStatus.PROCESSING
    with pytest.raises(DomainError):
        map_oneclick_status("SOMETHING_NEW")
    assert not can_transition(InvoiceStatus.PAID, InvoiceStatus.REFUNDED)
    assert can_transition(InvoiceStatus.AWAITING_PAYMENT, InvoiceStatus.PAID)
