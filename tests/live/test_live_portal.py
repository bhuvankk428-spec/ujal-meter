"""Opt-in read-only smoke tests. Never run in CI.

RUN_LIVE_TESTS=1 URJA_BASE_URL=... URJA_USERNAME=... URJA_PASSWORD=... \
    pytest -m live tests/live
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.live

if os.getenv("RUN_LIVE_TESTS") != "1":
    pytest.skip("set RUN_LIVE_TESTS=1 to run read-only live checks", allow_module_level=True)


def test_live_smoke() -> None:
    settings = Settings()
    app = create_app(settings)
    with TestClient(app) as client:
        ready = client.get("/ready")
        assert ready.status_code == 200, ready.text

        page = client.get("/api/v1/meters", params={"page_size": 1})
        assert page.status_code == 200, page.text
        items = page.json()["items"]
        assert items

        detail = client.get(f"/api/v1/meters/{items[0]['id']}")
        assert detail.status_code == 200, detail.text
