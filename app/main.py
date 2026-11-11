"""HTTP layer. Maps domain/provider errors to status codes; no business logic here."""

from __future__ import annotations

import logging
import os
import re
import secrets
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import Agent
from .config import ROOT, Settings, get_settings
from .domain import DomainError
from .llm import LLM, make_llm
from .oneclick import LiveOneClick, MockOneClick, OneClick, OneClickError
from .payments import Payments
from .store import Store

log = logging.getLogger("aishtat")
WEB = ROOT / "web"
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class ChatIn(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=2000)


class PayIn(BaseModel):
    origin_asset: str
    refund_to: str = Field(min_length=2, max_length=200)


class TxIn(BaseModel):
    tx_hash: str = Field(min_length=8, max_length=200)


class ReplyIn(BaseModel):
    text: str = Field(default="", max_length=2000)
    release: bool = False  # hand the chat back to the AI


def public_invoice(inv: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in inv.items() if k not in ("session_id",)}
    if out.get("payment"):
        out["payment"] = {k: v for k, v in out["payment"].items() if k not in ("checked_at",)}
    return out


def create_app(
    settings: Settings | None = None,
    oneclick: OneClick | None = None,
    llm: LLM | None = None,
    store: Store | None = None,
) -> FastAPI:
    s = settings or get_settings()
    storage_error = ""
    if store is None:
        try:
            store = Store(s.db_path, s.turso_url, s.turso_token)
        except Exception as exc:  # bad Turso URL/token must not take the whole site down
            storage_error = f"{type(exc).__name__}: {exc}"[:300]
            log.error("Shared database unavailable, falling back to local SQLite: %s", storage_error)
            store = Store("/tmp/app.db" if os.getenv("VERCEL") else s.db_path)
    oneclick = oneclick or (
        LiveOneClick(s.oneclick_base_url, s.oneclick_api_key) if s.oneclick_mode == "live" else MockOneClick(store)
    )
    llm = llm or make_llm(s)
    agent = Agent(s, store, llm)
    payments = Payments(s, store, oneclick)

    app = FastAPI(title="AI-Shtat Pay", version="0.1.0")
    app.state.settings, app.state.store, app.state.oneclick = s, store, oneclick

    def check_session(sid: str) -> str:
        if not SESSION_RE.match(sid):
            raise HTTPException(400, "Bad session id")
        return sid

    def merchant_auth(token: str = Query(default="")) -> None:
        if s.dashboard_token and not secrets.compare_digest(token.encode(), s.dashboard_token.encode()):
            raise HTTPException(401, "Bad dashboard token")

    @app.exception_handler(DomainError)
    async def _domain(_, exc: DomainError):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(Exception)
    async def _unexpected(request, exc: Exception):  # type: ignore[no-untyped-def]
        # A bare 500 hides the cause on serverless; show type + message (no secrets
        # live in exception text here) and log the full traceback.
        from fastapi.responses import JSONResponse

        log.exception("Unhandled error on %s", request.url.path)
        return JSONResponse({"detail": f"Internal error: {type(exc).__name__}: {exc}"[:500]}, status_code=500)

    @app.exception_handler(OneClickError)
    async def _oneclick(_, exc: OneClickError):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        code = 404 if exc.status_code == 404 else 502
        return JSONResponse({"detail": str(exc)}, status_code=code)

    # pages ----------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    @app.get("/merchant", include_in_schema=False)
    def merchant_page() -> FileResponse:
        return FileResponse(WEB / "merchant.html")

    # public API -------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "oneclick_mode": oneclick.mode,
            "llm": llm.name,
            "storage": store.backend,
            **({"storage_error": storage_error} if storage_error else {}),
            "merchant": s.merchant["name"],
            "settles_to": {"account": s.merchant_near_account, "asset": s.usdc_asset_id},
        }

    @app.post("/api/chat")
    async def chat(body: ChatIn) -> dict[str, Any]:
        sid = check_session(body.session_id)
        new = await agent.handle(sid, body.message.strip())
        session = store.get_session(sid) or {}
        return {"messages": [_msg(m, store) for m in new], "mode": session.get("mode", "ai")}

    @app.get("/api/chat/{sid}/messages")
    def chat_messages(sid: str, after: int = 0) -> dict[str, Any]:
        check_session(sid)
        session = store.get_session(sid) or {}
        msgs = store.messages(sid, after_id=after, visible_only=True)
        return {"messages": [_msg(m, store) for m in msgs], "mode": session.get("mode", "ai")}

    @app.get("/api/tokens")
    async def tokens() -> list[dict[str, Any]]:
        return await payments.payable_tokens()

    @app.get("/api/invoices/{invoice_id}")
    async def get_invoice(invoice_id: str) -> dict[str, Any]:
        return public_invoice(await payments.refresh(invoice_id))

    @app.post("/api/invoices/{invoice_id}/pay")
    async def pay(invoice_id: str, body: PayIn) -> dict[str, Any]:
        return public_invoice(await payments.start(invoice_id, body.origin_asset, body.refund_to))

    @app.post("/api/invoices/{invoice_id}/tx")
    async def submit_tx(invoice_id: str, body: TxIn) -> dict[str, Any]:
        return public_invoice(await payments.submit_tx(invoice_id, body.tx_hash))

    @app.post("/api/demo/invoices/{invoice_id}/simulate-deposit")
    async def simulate(invoice_id: str) -> dict[str, Any]:
        if not isinstance(oneclick, MockOneClick):
            raise HTTPException(403, "Only available in mock mode")
        inv = store.get_invoice(invoice_id)
        if not inv or not inv.get("payment"):
            raise HTTPException(404, "No payment started for this invoice")
        oneclick.simulate_deposit(inv["payment"]["deposit_address"])
        return public_invoice(await payments.refresh(invoice_id, force=True))

    # merchant API -----------------------------------------------------------
    @app.get("/api/merchant/overview", dependencies=[Depends(merchant_auth)])
    async def overview() -> dict[str, Any]:
        invoices = store.list_invoices()
        paid = [i for i in invoices if i["status"] == "PAID"]
        return {
            "merchant": s.merchant,
            "settles_to": s.merchant_near_account,
            "stats": {
                "invoices": len(invoices),
                "paid": len(paid),
                "revenue_usd": str(sum((_dec(i["total_usd"]) for i in paid), _dec("0"))),
            },
            "invoices": invoices,
            "handoffs": store.list_handoffs(),
            "sessions": store.list_sessions(),
        }

    @app.get("/api/merchant/sessions/{sid}", dependencies=[Depends(merchant_auth)])
    def merchant_session(sid: str) -> dict[str, Any]:
        check_session(sid)
        return {"session": store.get_session(sid), "messages": store.messages(sid, visible_only=True)}

    @app.post("/api/merchant/sessions/{sid}/reply", dependencies=[Depends(merchant_auth)])
    def merchant_reply(sid: str, body: ReplyIn) -> dict[str, Any]:
        check_session(sid)
        if not store.get_session(sid):
            raise HTTPException(404, "No such session")
        if body.text.strip():
            store.add_message(sid, "manager", body.text.strip())
        if body.release:
            store.set_mode(sid, "ai")
            store.resolve_handoffs(sid)
        else:
            store.set_mode(sid, "human")
        return {"ok": True, "mode": (store.get_session(sid) or {}).get("mode")}

    return app


def _dec(v: str):  # small helper to keep Decimal import local
    from decimal import Decimal

    return Decimal(v)


def _msg(m: dict[str, Any], store: Store) -> dict[str, Any]:
    out = {"id": m["id"], "role": m["role"], "content": m["content"], "created_at": m["created_at"]}
    ids = m["meta"].get("invoice_ids") or []
    if ids:
        out["invoices"] = [public_invoice(i) for i in (store.get_invoice(x) for x in ids) if i]
    return out


app = create_app()  # uvicorn app.main:app
