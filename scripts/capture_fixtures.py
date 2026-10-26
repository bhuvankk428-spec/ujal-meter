"""Capture representative mock-portal responses into tests/fixtures.

Fixtures are scrubbed of cookies/tokens and are safe to commit. Re-run this
script if the mock portal's HTML/CSV changes.

Usage: python scripts/capture_fixtures.py
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

from mock_portal.app import app as mock_app

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
BASE = "https://portal.fixture.invalid"


def scrub(text: str) -> str:
    return re.sub(
        r'(name="csrf_token" value=")[^"]+(")',
        r"\1fixture-csrf-token\2",
        text,
    )


async def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    transport = httpx.ASGITransport(app=mock_app)
    async with httpx.AsyncClient(
        transport=transport, base_url=BASE, follow_redirects=False
    ) as client:
        login_page = await client.get("/login")
        (FIXTURES / "login.html").write_text(scrub(login_page.text), encoding="utf-8")

        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text)
        assert csrf is not None
        response = await client.post(
            "/login",
            data={
                "email": "operator@urja.example",
                "password": "urja-demo-pass",
                "csrf_token": csrf.group(1),
            },
        )
        assert response.status_code == 303, response.text

        meters_list = await client.get("/meters", params={"page": 1})
        (FIXTURES / "meters_list_page1.html").write_text(scrub(meters_list.text), encoding="utf-8")

        detail = await client.get("/meters/MTR-0001")
        (FIXTURES / "meter_detail.html").write_text(scrub(detail.text), encoding="utf-8")

        hierarchy = await client.get("/hierarchy")
        (FIXTURES / "hierarchy.html").write_text(scrub(hierarchy.text), encoding="utf-8")

        export = await client.get("/export/meters.csv")
        (FIXTURES / "meters.csv").write_text(export.text, encoding="utf-8")

        consumption = await client.get(
            "/meters/MTR-0014/consumption/data",
            params={
                "from": datetime(2026, 2, 1, tzinfo=UTC).isoformat(),
                "to": datetime(2026, 2, 7, tzinfo=UTC).isoformat(),
                "granularity": "daily",
            },
        )
        (FIXTURES / "consumption.json").write_text(consumption.text + "\n", encoding="utf-8")

    print(f"Wrote fixtures to {FIXTURES}")


if __name__ == "__main__":
    asyncio.run(main())
