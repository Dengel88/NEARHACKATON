"""Live clients against a fake HTTP transport: request shapes match the docs."""
import asyncio
import json

import httpx

from app.llm import OpenAICompatible
from app.oneclick import LiveOneClick


def test_oneclick_quote_sends_api_key_and_body():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["key"] = req.headers.get("x-api-key")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"quote": {"depositAddress": "0xabc"}})

    oc = LiveOneClick("https://1click.example", "KEY", transport=httpx.MockTransport(handler))
    out = asyncio.run(oc.quote({"swapType": "EXACT_OUTPUT"}))
    assert seen == {"path": "/v0/quote", "key": "KEY", "body": {"swapType": "EXACT_OUTPUT"}}
    assert out["quote"]["depositAddress"] == "0xabc"


def test_oneclick_error_message_surfaces():
    oc = LiveOneClick("https://1click.example", transport=httpx.MockTransport(
        lambda r: httpx.Response(400, json={"message": "amount too low"})))
    try:
        asyncio.run(oc.status("0xabc"))
    except Exception as exc:  # noqa: BLE001
        assert "amount too low" in str(exc)
    else:
        raise AssertionError("expected error")


def test_openai_compatible_parses_tool_calls_and_strips_think():
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert body["tools"] and req.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "<think>hmm</think>Sure",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "get_catalog", "arguments": "{}"}}]}}]})

    llm = OpenAICompatible("https://llm.example/v1", "k", "m", transport=httpx.MockTransport(handler))
    out = asyncio.run(llm.complete([{"role": "user", "content": "hi"}], [{"type": "function"}]))
    assert out["content"] == "Sure"
    assert out["tool_calls"][0]["name"] == "get_catalog"


def test_gemini_provider_uses_openai_compatible_endpoint(settings):
    from dataclasses import replace

    from app.llm import GEMINI_BASE_URL, make_llm

    llm = make_llm(replace(settings, llm_provider="gemini", gemini_api_key="g-key", gemini_model="gemini-x"))
    assert llm.name == "openai-compatible:gemini-x"
    assert str(llm._client.base_url).rstrip("/") == GEMINI_BASE_URL
    assert llm._client.headers["authorization"] == "Bearer g-key"


def test_gemini_thought_signature_is_echoed_back():
    """Gemini 3 rejects a follow-up turn whose tool call lacks its thought_signature."""
    from app.agent import _wire_call

    sig = {"google": {"thought_signature": "SIG123"}}

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "extra_content": sig,
             "function": {"name": "show_catalog", "arguments": "{}"}}]}}]})

    llm = OpenAICompatible("https://llm.example/v1", "k", "m", transport=httpx.MockTransport(handler))
    out = asyncio.run(llm.complete([{"role": "user", "content": "hi"}], []))
    call = out["tool_calls"][0]
    assert call["extra_content"] == sig
    assert _wire_call(call)["extra_content"] == sig
    assert "extra_content" not in _wire_call({"id": "x", "name": "n", "arguments": {}})
