"""Export the app's OpenAPI document.

CI regenerates this file and fails on drift, so /docs, /reference, openapi.json
and the running app always describe the same contract.

Usage: python scripts/export_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    app = create_app(Settings())
    output = ROOT / "openapi.json"
    output.write_text(
        json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
