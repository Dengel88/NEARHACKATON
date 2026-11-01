"""Business rules, in code.

The language model chooses *what* the customer wants (SKUs and quantities).
Everything that touches money is decided here: prices, totals, settlement
units, and which payment states are final. A model that hallucinates a price
or a SKU gets a DomainError back, never an invoice.
"""

from __future__ import annotations

import secrets
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any

from .config import Settings

MAX_QTY = 100
MAX_LINES = 10


class DomainError(ValueError):
    """A request that breaks a business rule. Safe to show to the model and the user."""


class InvoiceStatus(str, Enum):
    DRAFT = "DRAFT"                      # issued, customer has not picked a token yet
    AWAITING_PAYMENT = "AWAITING_PAYMENT"  # quote taken, deposit address shown
    PARTIAL = "PARTIAL"                  # deposit below the required minimum
    PROCESSING = "PROCESSING"            # deposit seen, swap/settlement running
    PAID = "PAID"                        # USDC delivered to the merchant
    REFUNDED = "REFUNDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"                  # quote deadline passed with no deposit

    @property
    def final(self) -> bool:
        return self in FINAL_STATUSES


FINAL_STATUSES = {
    InvoiceStatus.PAID,
    InvoiceStatus.REFUNDED,
    InvoiceStatus.FAILED,
    InvoiceStatus.EXPIRED,
}

# 1Click execution status -> invoice status.
ONECLICK_STATUS_MAP = {
    "PENDING_DEPOSIT": InvoiceStatus.AWAITING_PAYMENT,
    "KNOWN_DEPOSIT_TX": InvoiceStatus.PROCESSING,
    "INCOMPLETE_DEPOSIT": InvoiceStatus.PARTIAL,
    "PROCESSING": InvoiceStatus.PROCESSING,
    "SUCCESS": InvoiceStatus.PAID,
    "REFUNDED": InvoiceStatus.REFUNDED,
    "FAILED": InvoiceStatus.FAILED,
}


def map_oneclick_status(status: str) -> InvoiceStatus:
    try:
        return ONECLICK_STATUS_MAP[status]
    except KeyError as exc:  # unknown future status: never treat as paid
        raise DomainError(f"Unknown 1Click status: {status}") from exc


def can_transition(current: InvoiceStatus, new: InvoiceStatus) -> bool:
    """Final states never change. PAID is the only state the merchant ships on."""
    if current.final:
        return current == new
    return True


def to_units(amount: Decimal, decimals: int) -> int:
    """USD amount -> integer smallest units (USDC has 6 decimals)."""
    q = (amount * (Decimal(10) ** decimals)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(q)


def from_units(units: int | str, decimals: int) -> Decimal:
    return Decimal(int(units)) / (Decimal(10) ** decimals)


def new_invoice_id() -> str:
    return "inv_" + secrets.token_urlsafe(8).replace("-", "x").replace("_", "y")


def build_invoice_lines(settings: Settings, lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Decimal]:
    """Validate model-chosen lines against the catalog and price them server-side."""
    if not lines:
        raise DomainError("An invoice needs at least one item.")
    if len(lines) > MAX_LINES:
        raise DomainError(f"At most {MAX_LINES} different items per invoice.")

    merged: dict[str, int] = {}
    for line in lines:
        sku = str(line.get("sku", "")).strip()
        try:
            qty = int(line.get("qty", 1))
        except (TypeError, ValueError) as exc:
            raise DomainError(f"Quantity for '{sku}' must be a whole number.") from exc
        if settings.item(sku) is None:
            known = ", ".join(i.sku for i in settings.catalog)
            raise DomainError(f"Unknown SKU '{sku}'. Known SKUs: {known}.")
        if qty < 1 or qty > MAX_QTY:
            raise DomainError(f"Quantity for '{sku}' must be between 1 and {MAX_QTY}.")
        merged[sku] = merged.get(sku, 0) + qty
        if merged[sku] > MAX_QTY:
            raise DomainError(f"Quantity for '{sku}' must be between 1 and {MAX_QTY}.")

    priced = []
    total = Decimal("0")
    for sku, qty in merged.items():
        item = settings.item(sku)
        assert item is not None
        line_total = item.price_usd * qty
        total += line_total
        priced.append(
            {
                "sku": sku,
                "name": item.name,
                "qty": qty,
                "unit_price_usd": str(item.price_usd),
                "line_total_usd": str(line_total),
            }
        )
    return priced, total


def build_quote_request(
    settings: Settings,
    *,
    origin_asset: str,
    refund_to: str,
    amount_units: int,
    deadline_iso: str,
    dry: bool = False,
) -> dict[str, Any]:
    """EXACT_OUTPUT: the merchant receives exactly the invoice total in USDC on NEAR.

    The customer pays whatever amount of their token covers that; excess above
    the quoted amountIn is refunded by 1Click to `refund_to` on the origin chain.
    """
    if not origin_asset:
        raise DomainError("Pick a token to pay with.")
    if not refund_to or len(refund_to) < 2:
        raise DomainError("A refund address on the paying chain is required.")
    if amount_units <= 0:
        raise DomainError("Invoice total must be positive.")
    return {
        "dry": dry,
        "swapType": "EXACT_OUTPUT",
        "slippageTolerance": int(settings.payments.get("slippage_bps", 100)),
        "originAsset": origin_asset,
        "depositType": "ORIGIN_CHAIN",
        "destinationAsset": settings.usdc_asset_id,
        "amount": str(amount_units),
        "refundTo": refund_to,
        "refundType": "ORIGIN_CHAIN",
        "recipient": settings.merchant_near_account,
        "recipientType": "DESTINATION_CHAIN",
        "deadline": deadline_iso,
    }
