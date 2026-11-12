"""Turso/libSQL backend against an in-process Hrana-over-HTTP fake.

The fake follows the /v2/pipeline wire format (typed values, integers as
strings, last_insert_rowid). Two app instances share it, the way two Vercel
function instances share one Turso database.
"""
import base64
import sqlite3

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.oneclick import MockOneClick
from app.store import Store, TursoBackend


class FakeHrana:
    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.calls = 0

    @staticmethod
    def _in(v):
        t = v["type"]
        if t == "integer":
            assert isinstance(v["value"], str)
            return int(v["value"])
        if t == "float":
            return float(v["value"])
        if t == "blob":
            return base64.b64decode(v["base64"])
        return None if t == "null" else v["value"]

    @staticmethod
    def _out(v):
        if v is None:
            return {"type": "null"}
        if isinstance(v, int):
            return {"type": "integer", "value": str(v)}
        if isinstance(v, float):
            return {"type": "float", "value": v}
        return {"type": "text", "value": v}

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/pipeline"
        assert request.headers["authorization"] == "Bearer tok"
        self.calls += 1
        results = []
        for req in __import__("json").loads(request.content)["requests"]:
            if req["type"] == "close":
                results.append({"type": "ok", "response": {"type": "close"}})
                continue
            stmt = req["stmt"]
            try:
                cur = self.db.execute(stmt["sql"], [self._in(a) for a in stmt.get("args", [])])
                rows = cur.fetchall()
                self.db.commit()
            except sqlite3.Error as e:
                results.append({"type": "error", "error": {"message": str(e)}})
                continue
            cols = [{"name": d[0], "decltype": None} for d in (cur.description or [])]
            results.append({"type": "ok", "response": {"type": "execute", "result": {
                "cols": cols,
                "rows": [[self._out(v) for v in r] for r in rows],
                "affected_row_count": cur.rowcount,
                "last_insert_rowid": str(cur.lastrowid) if cur.lastrowid else None,
            }}})
        return httpx.Response(200, json={"baton": None, "base_url": None, "results": results})



def _turso_store(fake: FakeHrana, monkeypatch) -> Store:
    real = httpx.Client

    def client(**kw):
        return real(transport=httpx.MockTransport(fake.handle), **kw)

    monkeypatch.setattr(httpx, "Client", client)
    try:
        return Store(turso_url="libsql://demo.turso.io", turso_token="tok")
    finally:
        monkeypatch.setattr(httpx, "Client", real)


def test_url_scheme_is_rewritten():
    b = TursoBackend("libsql://db-org.turso.io")
    assert str(b._http.base_url).startswith("https://db-org.turso.io")


def test_typed_values_roundtrip(monkeypatch):
    fake = FakeHrana()
    s = _turso_store(fake, monkeypatch)
    assert s.backend == "turso"
    s.ensure_session("sess-abcdef")
    mid = s.add_message("sess-abcdef", "user", "привет", {"a": 1})
    assert mid == 1
    msg = s.messages("sess-abcdef")[0]
    assert msg["content"] == "привет" and msg["meta"] == {"a": 1} and msg["visible"] is True
    assert isinstance(msg["created_at"], float)
    s.kv_set("k", {"x": [1, 2]})
    assert s.kv_get("k") == {"x": [1, 2]} and s.kv_get("missing") is None


def test_two_instances_share_state(settings, monkeypatch):
    fake = FakeHrana()

    def instance():
        store = _turso_store(fake, monkeypatch)
        oc = MockOneClick(store)
        oc.SETTLE_SECONDS = 0
        return TestClient(create_app(settings=settings, oneclick=oc, store=store))

    a, b = instance(), instance()
    sid = "shared-session-1"
    msg = a.post("/api/chat", json={"session_id": sid, "message": "I want to buy 1 coffee"}).json()["messages"][-1]
    inv = msg["invoices"][0]

    # instance B sees the chat and invoice created on A
    assert b.get(f"/api/chat/{sid}/messages").json()["messages"]
    sol = next(t for t in b.get("/api/tokens").json() if t["symbol"] == "SOL")
    r = b.post(f"/api/invoices/{inv['id']}/pay", json={"origin_asset": sol["assetId"], "refund_to": "So1anaRefund1"})
    assert r.status_code == 200, r.text

    # the mock deposit simulated on A settles the quote made on B
    done = a.post(f"/api/demo/invoices/{inv['id']}/simulate-deposit").json()
    assert done["status"] == "PAID"
    assert b.get("/api/merchant/overview").json()["stats"]["paid"] == 1


def test_bad_turso_settings_do_not_crash_the_app(settings):
    s = __import__("dataclasses").replace(settings, turso_url="https://127.0.0.1:9", turso_token="bad", db_path=":memory:")
    h = TestClient(create_app(settings=s)).get("/api/health").json()
    assert h["storage"] == "sqlite" and "storage_error" in h
