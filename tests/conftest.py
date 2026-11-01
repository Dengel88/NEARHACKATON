import os

os.environ.setdefault("DB_PATH", ":memory:")
os.environ["ONECLICK_MODE"] = "mock"
os.environ["LLM_PROVIDER"] = "mock"

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.oneclick import MockOneClick
from app.store import Store


@pytest.fixture
def settings():
    return load_settings(db_path=":memory:", oneclick_mode="mock", llm_provider="mock")


@pytest.fixture
def oneclick():
    return MockOneClick()


@pytest.fixture
def client(settings, oneclick):
    app = create_app(settings=settings, oneclick=oneclick, store=Store(":memory:"))
    return TestClient(app)
