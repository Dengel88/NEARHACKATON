"""The sales assistant: a tool-calling loop over the LLM.

The model has three tools and no other way to affect anything:
get_catalog, create_invoice (SKUs + quantities only; the server prices it),
and handoff_to_manager. Tool traffic is stored but hidden from the customer.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .config import Settings
from .domain import DomainError, InvoiceStatus, build_invoice_lines, new_invoice_id, to_units
from .llm import LLM, LLMError
from .store import Store

log = logging.getLogger(__name__)
MAX_STEPS = 4
HISTORY_LIMIT = 40

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_catalog",
            "description": "List the merchant's products and services with prices in USD.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_invoice",
            "description": (
                "Issue an invoice for the customer. Pass catalog SKUs and quantities only; "
                "prices are computed by the server. The customer pays it in the chat widget "
                "with any supported token on any chain."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sku": {"type": "string"},
                                "qty": {"type": "integer", "minimum": 1},
                            },
                            "required": ["sku", "qty"],
                        },
                    },
                    "customer_note": {"type": "string", "description": "Optional note: name, contact, wishes."},
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handoff_to_manager",
            "description": (
                "Hand the conversation to a human manager. Use when the customer asks for a person, "
                "has a complaint, wants a custom deal, or you cannot answer from the catalog."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "contact": {"type": "string", "description": "Customer contact if they gave one."},
                },
                "required": ["reason"],
            },
        },
    },
]


def system_prompt(settings: Settings) -> str:
    m = settings.merchant
    return f"""You are the AI sales assistant of {m['name']} ({m.get('tagline', '')}).

Rules:
- Always answer in the customer's language. You speak: {', '.join(m.get('languages', ['en']))}.
- Be brief and friendly: 1-4 sentences.
- Never state a price you did not get from get_catalog in this conversation. Call it first.
- When the customer wants to buy, call create_invoice with catalog SKUs and quantities.
  Do not compute totals yourself; repeat the total the tool returns.
- Payment: the invoice appears in the chat; the customer can pay with almost any token on
  any chain (ETH, SOL, BTC, USDT on Tron, NEAR...). It is routed through NEAR Intents and
  the merchant receives USDC on NEAR. You never handle keys or ask for seed phrases.
- Call handoff_to_manager when the customer asks for a human, complains, wants a custom
  deal, or asks something you cannot answer from the catalog. The manager is {m.get('manager_name', 'a manager')}.
"""


class Agent:
    def __init__(self, settings: Settings, store: Store, llm: LLM):
        self.s = settings
        self.store = store
        self.llm = llm

    # --- tools ------------------------------------------------------------
    def _tool_get_catalog(self, session_id: str, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "items": [
                {"sku": i.sku, "name": i.name, "description": i.description, "price_usd": str(i.price_usd)}
                for i in self.s.catalog
            ]
        }

    def _tool_create_invoice(self, session_id: str, args: dict[str, Any]) -> dict[str, Any]:
        lines, total = build_invoice_lines(self.s, list(args.get("items") or []))
        decimals = int(self.s.payments.get("settlement_decimals", 6))
        invoice = {
            "id": new_invoice_id(),
            "session_id": session_id,
            "status": InvoiceStatus.DRAFT.value,
            "lines": lines,
            "total_usd": str(total),
            "settlement": {
                "asset_id": self.s.usdc_asset_id,
                "symbol": self.s.payments.get("settlement_symbol", "USDC"),
                "decimals": decimals,
                "amount_units": str(to_units(total, decimals)),
                "recipient": self.s.merchant_near_account,
            },
            "customer_note": str(args.get("customer_note") or "")[:500],
            "payment": None,
            "history": [],
        }
        self.store.save_invoice(invoice)
        return {"invoice_id": invoice["id"], "total_usd": invoice["total_usd"], "lines": lines}

    def _tool_handoff_to_manager(self, session_id: str, args: dict[str, Any]) -> dict[str, Any]:
        reason = str(args.get("reason") or "customer request")[:500]
        contact = str(args.get("contact") or "")[:200]
        self.store.add_handoff(session_id, reason, contact)
        self.store.set_mode(session_id, "human")
        notify_manager(self.s, session_id, reason, contact)
        return {"ok": True, "manager": self.s.merchant.get("manager_name", "manager")}

    def run_tool(self, session_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = getattr(self, f"_tool_{name}", None)
        if fn is None:
            return {"error": f"Unknown tool {name}"}
        try:
            return fn(session_id, args)
        except DomainError as exc:
            return {"error": str(exc)}

    # --- loop -------------------------------------------------------------
    def _history(self, session_id: str) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = [{"role": "system", "content": system_prompt(self.s)}]
        rows = self.store.messages(session_id)[-HISTORY_LIMIT:]
        # never start the window on an orphan tool result
        while rows and rows[0]["role"] == "tool":
            rows = rows[1:]
        for r in rows:
            role = r["role"]
            if role == "manager":
                msgs.append({"role": "assistant", "content": f"[manager {self.s.merchant.get('manager_name', '')}]: {r['content']}"})
            elif role == "assistant" and r["meta"].get("tool_calls"):
                msgs.append(
                    {
                        "role": "assistant",
                        "content": r["content"] or None,
                        "tool_calls": [
                            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                            for c in r["meta"]["tool_calls"]
                        ],
                    }
                )
            elif role == "tool":
                msgs.append({"role": "tool", "tool_call_id": r["meta"]["tool_call_id"], "name": r["meta"]["name"], "content": r["content"]})
            elif role in ("user", "assistant"):
                msgs.append({"role": role, "content": r["content"]})
        return msgs

    async def handle(self, session_id: str, text: str) -> list[dict[str, Any]]:
        """Store the user's message and, unless a human has taken over, reply.

        Returns the new visible messages (assistant replies) produced this turn.
        """
        self.store.ensure_session(session_id)
        first_new = self.store.add_message(session_id, "user", text)
        session = self.store.get_session(session_id)
        if session and session["mode"] == "human":
            return []

        invoices_created: list[str] = []
        for _ in range(MAX_STEPS):
            try:
                out = await self.llm.complete(self._history(session_id), TOOLS)
            except LLMError as exc:
                log.warning("LLM failed: %s", exc)
                self.store.add_message(
                    session_id, "assistant",
                    "Sorry, I'm having trouble right now. A manager will get back to you.",
                    meta={"error": True},
                )
                break
            if not out["tool_calls"]:
                self.store.add_message(
                    session_id, "assistant", out["content"] or "", meta={"invoice_ids": invoices_created}
                )
                break
            self.store.add_message(
                session_id, "assistant", out["content"] or "", meta={"tool_calls": out["tool_calls"]}, visible=False
            )
            for call in out["tool_calls"]:
                result = self.run_tool(session_id, call["name"], call["arguments"])
                if call["name"] == "create_invoice" and "invoice_id" in result:
                    invoices_created.append(result["invoice_id"])
                self.store.add_message(
                    session_id, "tool", json.dumps(result, ensure_ascii=False),
                    meta={"tool_call_id": call["id"], "name": call["name"]}, visible=False,
                )
        else:
            self.store.add_message(session_id, "assistant", "…", meta={"invoice_ids": invoices_created})

        return [m for m in self.store.messages(session_id, after_id=first_new, visible_only=True)]


def notify_manager(settings: Settings, session_id: str, reason: str, contact: str) -> None:
    """Best-effort Telegram ping. Never blocks or fails the chat."""
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return
    text = f"🙋 Handoff from AI assistant\nSession: {session_id}\nReason: {reason}\nContact: {contact or '-'}"
    try:
        httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text},
            timeout=5,
        )
    except httpx.HTTPError as exc:  # pragma: no cover - network
        log.warning("Telegram notify failed: %s", exc)
