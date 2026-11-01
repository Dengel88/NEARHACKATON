"""End-to-end in mock mode: chat -> invoice -> pay with any token -> PAID."""
import time

SID = "test-session-1"


def _chat(client, text, sid=SID):
    r = client.post("/api/chat", json={"session_id": sid, "message": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_health(client):
    h = client.get("/api/health").json()
    assert h["oneclick_mode"] == "mock" and h["llm"] == "mock"


def test_catalog_question_answered_with_server_prices(client):
    out = _chat(client, "What are your prices?")
    text = out["messages"][-1]["content"]
    assert "$390.00" in text and "$149.00" in text


def test_russian_customer_gets_russian_reply(client):
    out = _chat(client, "Сколько стоит лендинг?")
    assert "Вот что мы предлагаем" in out["messages"][-1]["content"]


def test_full_payment_flow(client, oneclick):
    out = _chat(client, "I want to buy 2 coffee")
    msg = out["messages"][-1]
    assert msg["invoices"], msg
    inv = msg["invoices"][0]
    assert inv["status"] == "DRAFT" and inv["total_usd"] == "2.00"
    assert inv["settlement"]["amount_units"] == "2000000"

    tokens = client.get("/api/tokens").json()
    sol = next(t for t in tokens if t["blockchain"] == "sol" and t["symbol"] == "SOL")

    r = client.post(f"/api/invoices/{inv['id']}/pay", json={"origin_asset": sol["assetId"], "refund_to": "So1anaRefundAddr111"})
    assert r.status_code == 200, r.text
    paying = r.json()
    assert paying["status"] == "AWAITING_PAYMENT"
    assert paying["payment"]["deposit_address"]
    assert paying["payment"]["quote_signature"]

    # cannot start a second payment once one is in progress
    client.post(f"/api/demo/invoices/{inv['id']}/simulate-deposit")
    again = client.post(f"/api/invoices/{inv['id']}/pay", json={"origin_asset": sol["assetId"], "refund_to": "x1"})
    assert again.status_code == 422

    oneclick.SETTLE_SECONDS = 0
    time.sleep(0.01)
    done = client.get(f"/api/invoices/{inv['id']}").json()
    # refresh is rate-limited; force through simulate endpoint once more
    done = client.post(f"/api/demo/invoices/{inv['id']}/simulate-deposit").json()
    assert done["status"] == "PAID"
    assert [h["status"] for h in done["history"]][-1] == "PAID"

    ov = client.get("/api/merchant/overview").json()
    assert ov["stats"]["paid"] == 1 and ov["stats"]["revenue_usd"] == "2.00"


def test_unknown_token_rejected(client):
    inv = _chat(client, "buy 1 coffee")["messages"][-1]["invoices"][0]
    r = client.post(f"/api/invoices/{inv['id']}/pay", json={"origin_asset": "nep141:evil.near", "refund_to": "alice.near"})
    assert r.status_code == 422


def test_handoff_silences_ai_until_released(client):
    out = _chat(client, "Can I talk to a manager?")
    assert out["mode"] == "human"
    assert client.get("/api/merchant/overview").json()["handoffs"]
    # AI stays quiet while a human owns the chat
    assert _chat(client, "hello?")["messages"] == []
    client.post(f"/api/merchant/sessions/{SID}/reply", json={"text": "Hi, Sam here!"})
    msgs = client.get(f"/api/chat/{SID}/messages").json()["messages"]
    assert msgs[-1]["role"] == "manager"
    client.post(f"/api/merchant/sessions/{SID}/reply", json={"release": True})
    assert _chat(client, "prices?")["mode"] == "ai"


def test_bad_session_id(client):
    r = client.post("/api/chat", json={"session_id": "../x", "message": "hi"})
    assert r.status_code == 400
