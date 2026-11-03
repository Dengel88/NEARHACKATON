"""Settings: secrets and modes from the environment, business data from config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class CatalogItem:
    sku: str
    name: str
    description: str
    price_usd: Decimal


@dataclass(frozen=True)
class Settings:
    merchant: dict[str, Any]
    catalog: tuple[CatalogItem, ...]
    payments: dict[str, Any]

    oneclick_mode: str = "mock"
    oneclick_base_url: str = "https://1click.chaindefuser.com"
    oneclick_api_key: str = ""
    merchant_near_account: str = "merchant.near"
    usdc_asset_id: str = (
        "nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
    )

    llm_provider: str = "mock"
    llm_base_url: str = "https://cloud-api.near.ai/v1"
    llm_api_key: str = ""
    llm_model: str = "zai-org/GLM-5.1-FP8"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    db_path: str = "data/app.db"
    dashboard_token: str = ""

    extra: dict[str, Any] = field(default_factory=dict)

    def item(self, sku: str) -> CatalogItem | None:
        return next((i for i in self.catalog if i.sku == sku), None)


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def load_settings(config_path: Path | None = None, **overrides: Any) -> Settings:
    raw = yaml.safe_load((config_path or ROOT / "config.yaml").read_text("utf-8"))
    catalog = tuple(
        CatalogItem(
            sku=c["sku"],
            name=c["name"],
            description=c.get("description", ""),
            price_usd=Decimal(str(c["price_usd"])),
        )
        for c in raw["catalog"]
    )
    values: dict[str, Any] = dict(
        merchant=raw["merchant"],
        catalog=catalog,
        payments=raw["payments"],
        oneclick_mode=_env("ONECLICK_MODE", "mock"),
        oneclick_base_url=_env("ONECLICK_BASE_URL", "https://1click.chaindefuser.com"),
        oneclick_api_key=_env("ONECLICK_API_KEY", ""),
        merchant_near_account=_env("MERCHANT_NEAR_ACCOUNT", "merchant.near"),
        usdc_asset_id=_env("USDC_ASSET_ID", Settings.usdc_asset_id),
        llm_provider=_env("LLM_PROVIDER", "mock"),
        llm_base_url=_env("LLM_BASE_URL", "https://cloud-api.near.ai/v1"),
        llm_api_key=_env("LLM_API_KEY", ""),
        llm_model=_env("LLM_MODEL", "zai-org/GLM-5.1-FP8"),
        gemini_api_key=_env("GEMINI_API_KEY", _env("GOOGLE_API_KEY", "")),
        gemini_model=_env("GEMINI_MODEL", "gemini-2.5-flash"),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID", ""),
        db_path=_env("DB_PATH", "/tmp/app.db" if os.getenv("VERCEL") else "data/app.db"),
        dashboard_token=_env("MERCHANT_DASHBOARD_TOKEN", ""),
    )
    values.update(overrides)
    return Settings(**values)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
