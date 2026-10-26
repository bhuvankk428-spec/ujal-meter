from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]


def test_committed_openapi_matches_app() -> None:
    app = create_app(Settings())
    schema = app.openapi()
    committed = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    assert schema == committed


def test_openapi_documents_problem_details_and_admin_auth() -> None:
    schema = create_app(Settings()).openapi()
    problem = schema["components"]["schemas"]["ProblemDetail"]
    assert problem["example"]["status"] == 404
    assert schema["components"]["securitySchemes"]["ApiKeyAuth"]["name"] == "X-API-Key"
    assert schema["paths"]["/api/v1/admin/cache/refresh"]["post"]["security"] == [
        {"ApiKeyAuth": []}
    ]
    assert "503" in schema["paths"]["/api/v1/meters"]["get"]["responses"]
