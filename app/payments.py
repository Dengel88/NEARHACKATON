"""Invoice payment lifecycle on top of 1Click."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Settings
from .domain import (
    DomainError,
    InvoiceStatus,
    build_quote_request,
    can_transition,
    map_oneclick_status,
)
from .oneclick import OneClick
from .store import Store

REFRESH_MIN_SECONDS = 3.0


class Payments:
    def __init__(self, settings: Settings, store: Store, oneclick: OneClick):
        self.s = settings
        self.store = store
        self.oneclick = oneclick

    async def payable_tokens(self) -> list[dict[str, Any]]:
        offered = {(o["blockchain"], o["symbol"]) for o in self.s.payments.get("offered", [])}
        tokens = await self.oneclick.tokens()
        out = [
            {
                "assetId": t["assetId"],
                "symbol": t["symbol"],
                "blockchain": t["blockchain"],
                "decimals": t["decimals"],
                "price": t.get("price"),
            }
            for t in tokens
            if not offered or (t["blockchain"], t["symbol"]) in offered
        ]
        out.sort(key=lambda t: (t["blockchain"] != "near", t["blockchain"], t["symbol"]))
        return out

    def _get(self, invoice_id: str) -> dict[str, Any]:
        inv = self.store.get_invoice(invoice_id)
        if inv is None:
            raise DomainError("Invoice not found.")
        return inv

    async def start(self, invoice_id: str, origin_asset: str, refund_to: str) -> dict[str, Any]:
        """Take a binding 1Click quote and attach its deposit address to the invoice."""
        inv = self._get(invoice_id)
        status = InvoiceStatus(inv["status"])
        if status.final:
            raise DomainError(f"Invoice is already {status.value}.")
        if status in (InvoiceStatus.PROCESSING, InvoiceStatus.PARTIAL):
            raise DomainError("A payment for this invoice is already in progress.")
        valid_assets = {t["assetId"] for t in await self.payable_tokens()}
        if origin_asset not in valid_assets:
            raise DomainError("This token is not accepted.")

        ttl = int(self.s.payments.get("quote_ttl_minutes", 30))
        deadline = (datetime.now(timezone.utc) + timedelta(minutes=ttl)).strftime("%Y-%m-%dT%H:%M:%SZ")
        req = build_quote_request(
            self.s,
            origin_asset=origin_asset,
            refund_to=refund_to.strip(),
            amount_units=int(inv["settlement"]["amount_units"]),
            deadline_iso=deadline,
        )
        resp = await self.oneclick.quote(req)
        q = resp["quote"]
        if not q.get("depositAddress"):
            raise DomainError("1Click returned no deposit address.")
        token = next((t for t in await self.payable_tokens() if t["assetId"] == origin_asset), {})
        inv["payment"] = {
            "origin_asset": origin_asset,
            "origin_symbol": token.get("symbol"),
            "origin_chain": token.get("blockchain"),
            "refund_to": req["refundTo"],
            "deposit_address": q["depositAddress"],
            "deposit_memo": q.get("depositMemo"),
            "amount_in": q["amountIn"],
            "amount_in_formatted": q["amountInFormatted"],
            "amount_in_usd": q.get("amountInUsd"),
            "min_amount_in": q.get("minAmountIn"),
            "deadline": q.get("deadline") or deadline,
            "time_estimate": q.get("timeEstimate"),
            # kept for dispute resolution, as the 1Click docs advise
            "correlation_id": resp.get("correlationId"),
            "quote_signature": resp.get("signature"),
            "quote_timestamp": resp.get("timestamp"),
            "oneclick_status": "PENDING_DEPOSIT",
            "swap_details": None,
            "checked_at": 0.0,
            "mode": self.oneclick.mode,
        }
        self._set_status(inv, InvoiceStatus.AWAITING_PAYMENT, "quote taken")
        self.store.save_invoice(inv)
        return inv

    async def refresh(self, invoice_id: str, force: bool = False) -> dict[str, Any]:
        """Pull the latest 1Click status; rate-limited and a no-op for final invoices."""
        inv = self._get(invoice_id)
        pay = inv.get("payment")
        status = InvoiceStatus(inv["status"])
        if not pay or status.final:
            return inv
        if not force and time.time() - pay.get("checked_at", 0) < REFRESH_MIN_SECONDS:
            return inv
        st = await self.oneclick.status(pay["deposit_address"], pay.get("deposit_memo"))
        pay["oneclick_status"] = st["status"]
        pay["swap_details"] = st.get("swapDetails")
        pay["checked_at"] = time.time()
        new = map_oneclick_status(st["status"])
        if new == InvoiceStatus.AWAITING_PAYMENT and _past(pay.get("deadline")):
            new = InvoiceStatus.EXPIRED
        self._set_status(inv, new, f"1click:{st['status']}")
        self.store.save_invoice(inv)
        return inv

    async def submit_tx(self, invoice_id: str, tx_hash: str) -> dict[str, Any]:
        """Optional speed-up: tell 1Click about the deposit tx right away."""
        inv = self._get(invoice_id)
        pay = inv.get("payment")
        if not pay:
            raise DomainError("Start the payment first.")
        await self.oneclick.submit_deposit(tx_hash.strip(), pay["deposit_address"], pay.get("deposit_memo"))
        return await self.refresh(invoice_id, force=True)

    @staticmethod
    def _set_status(inv: dict[str, Any], new: InvoiceStatus, why: str) -> None:
        cur = InvoiceStatus(inv["status"])
        if cur == new or not can_transition(cur, new):
            return
        inv["status"] = new.value
        inv.setdefault("history", []).append({"at": time.time(), "status": new.value, "why": why})


def _past(iso: str | None) -> bool:
    if not iso:
        return False
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")) < datetime.now(timezone.utc)
    except ValueError:
        return False
