"""Chat model providers behind one small interface.

`OpenAICompatible` talks to any /v1/chat/completions endpoint with tool
calling; the default target is NEAR AI Cloud (private inference in TEEs).
`MockLLM` is a deterministic rule-based stand-in that emits the *same* tool
calls, so the agent loop, invoices and payments run offline and in tests.
"""

from __future__ import annotations

import json
import re
import uuid
from abc import ABC, abstractmethod
from typing import Any

import httpx

from .config import Settings


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


class LLMError(RuntimeError):
    pass


class LLM(ABC):
    name: str

    @abstractmethod
    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Return {"content": str | None, "tool_calls": [{"id", "name", "arguments": dict}]}."""


class OpenAICompatible(LLM):
    def __init__(self, base_url: str, api_key: str, model: str, transport: httpx.AsyncBaseTransport | None = None):
        if not api_key:
            raise LLMError("An API key is required (LLM_API_KEY or GEMINI_API_KEY)")
        self.name = f"openai-compatible:{model}"
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=90,
            transport=transport,
        )

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        body = {"model": self._model, "messages": messages, "tools": tools, "temperature": 0.3}
        try:
            resp = await self._client.post("/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise LLMError(f"LLM {resp.status_code}: {resp.text[:300]}")
        msg = resp.json()["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            raw = tc["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                args = {"_unparseable": raw}
            calls.append({"id": tc.get("id") or uuid.uuid4().hex, "name": tc["function"]["name"], "arguments": args})
        content = msg.get("content")
        if content:
            # reasoning models may leak <think> blocks into content
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        return {"content": content, "tool_calls": calls}


# --- mock -------------------------------------------------------------------

CYRILLIC = re.compile(r"[а-яё]", re.I)

SKU_KEYWORDS = {
    "assistant-pro": ["pro", "про", "telegram", "телеграм"],
    "assistant-start": ["start", "старт", "assistant", "ассистент", "бот", "bot"],
    "landing": ["landing", "лендинг", "сайт", "website", "site"],
    "promo-video": ["video", "видео", "ролик"],
    "demo-coffee": ["coffee", "кофе"],
}
BUY = ["buy", "order", "purchase", "invoice", "pay", "want", "take", "купить", "куплю", "закаж", "заказ", "оплат", "счёт", "счет", "хочу", "беру", "возьму"]
CATALOG = ["price", "cost", "catalog", "offer", "services", "what do you", "how much", "цен", "стоим", "сколько", "каталог", "услуг", "что есть", "прайс"]
HUMAN = ["manager", "human", "person", "operator", "менеджер", "человек", "оператор", "живой"]

TEXT = {
    "en": {
        "hello": "Hi! I'm the AI assistant of {merchant}. I can tell you about our services, put together an invoice, and you can pay it with almost any crypto on any chain. Want to see the catalog?",
        "catalog": "Here is what we offer:\n{lines}\nTell me what you'd like and I'll prepare an invoice.",
        "invoice": "Done! Invoice {id} for ${total} is ready below. Pick any token and chain you like: the payment is routed through NEAR Intents, and we receive USDC.",
        "handoff": "I've passed our conversation to {manager}, our manager. They'll reply right here in this chat.",
        "error": "I couldn't do that: {error}",
    },
    "ru": {
        "hello": "Здравствуйте! Я ИИ-ассистент {merchant}. Расскажу об услугах, соберу счёт, а оплатить его можно почти любой криптовалютой в любой сети. Показать каталог?",
        "catalog": "Вот что мы предлагаем:\n{lines}\nНапишите, что хотите заказать, и я выставлю счёт.",
        "invoice": "Готово! Счёт {id} на ${total} ниже. Выберите любой токен и сеть: оплата пройдёт через NEAR Intents, а мы получим USDC.",
        "handoff": "Передал диалог менеджеру ({manager}). Он ответит прямо в этом чате.",
        "error": "Не получилось: {error}",
    },
}


class MockLLM(LLM):
    name = "mock"

    def __init__(self, settings: Settings):
        self._s = settings

    @staticmethod
    def _lang(messages: list[dict[str, Any]]) -> str:
        for m in reversed(messages):
            if m["role"] == "user":
                return "ru" if CYRILLIC.search(m.get("content") or "") else "en"
        return "en"

    def _skus(self, text: str) -> list[dict[str, Any]]:
        low = text.lower()
        found: list[dict[str, Any]] = []
        for sku, words in SKU_KEYWORDS.items():
            for w in words:
                m = re.search(rf"(\d+)\s*(?:x\s*)?\S*{re.escape(w)}|{re.escape(w)}", low)
                if m:
                    qty = int(m.group(1)) if m.group(1) else 1
                    found.append({"sku": sku, "qty": qty})
                    low = low.replace(w, " ")
                    break
        # "pro" also matches the generic assistant words; keep the specific one
        if any(f["sku"] == "assistant-pro" for f in found):
            found = [f for f in found if f["sku"] != "assistant-start"]
        return found

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        lang = self._lang(messages)
        t = TEXT[lang]
        last = messages[-1]
        merchant = self._s.merchant["name"]

        if last["role"] == "tool":
            result = json.loads(last["content"])
            name = last.get("name")
            if "error" in result:
                return {"content": t["error"].format(error=result["error"]), "tool_calls": []}
            if name == "get_catalog":
                lines = "\n".join(f"• {i['name']}: ${i['price_usd']}" for i in result["items"])
                return {"content": t["catalog"].format(lines=lines), "tool_calls": []}
            if name == "create_invoice":
                return {"content": t["invoice"].format(id=result["invoice_id"], total=result["total_usd"]), "tool_calls": []}
            if name == "handoff_to_manager":
                return {"content": t["handoff"].format(manager=self._s.merchant.get("manager_name", "manager")), "tool_calls": []}
            return {"content": "OK", "tool_calls": []}

        text = (last.get("content") or "").lower()

        def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return {"content": None, "tool_calls": [{"id": uuid.uuid4().hex[:12], "name": name, "arguments": args}]}

        if any(w in text for w in HUMAN):
            return call("handoff_to_manager", {"reason": last.get("content", "")[:200]})
        skus = self._skus(text)
        if skus and any(w in text for w in BUY):
            return call("create_invoice", {"items": skus})
        if any(w in text for w in CATALOG) or skus:
            return call("get_catalog", {})
        return {"content": t["hello"].format(merchant=merchant), "tool_calls": []}


def make_llm(settings: Settings) -> LLM:
    if settings.llm_provider == "mock":
        return MockLLM(settings)
    if settings.llm_provider == "openai":
        return OpenAICompatible(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    if settings.llm_provider == "gemini":
        # Gemini's OpenAI-compatible endpoint: same tool-calling wire format
        return OpenAICompatible(GEMINI_BASE_URL, settings.gemini_api_key, settings.gemini_model)
    raise LLMError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")
