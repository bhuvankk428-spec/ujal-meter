# How to Run Locally

This project runs as two local processes:

1. `mock_portal` - a deterministic stand-in for the legacy Urja portal on port `9000`.
2. `app.main` - the public read-only API and web console on port `8000`.

No real portal, Docker, database, Redis, or Node.js is required for the local demo.

**Verified on Windows:** Python 3.13, fresh server processes, `/ready` returned `ready`, the API returned 24 meters from `bulk_csv`, and the dashboard returned HTTP 200.

## Prerequisites

- Python 3.11 or newer (`python --version`)
- PowerShell on Windows, or a normal shell on macOS/Linux
- Ports `8000` and `9000` available

## 1. One-time setup (Windows PowerShell)

Run these commands from the repository root, `D:\flork`:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Using `.\.venv\Scripts\python.exe` directly avoids PowerShell execution-policy problems with `Activate.ps1`.

## 2. Start the mock portal (terminal 1)

```powershell
.\.venv\Scripts\python.exe -m uvicorn mock_portal.app:app --host 127.0.0.1 --port 9000
```

Leave this terminal running. The mock exposes HTML pages, CSV export, login, and test control routes, but it is not part of the public API contract.

## 3. Start the API and console (terminal 2)

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Leave this terminal running too. The default configuration already points at `http://127.0.0.1:9000`, so no `.env` file is needed.

## 4. Open it

- Web console: <http://127.0.0.1:8000/>
- Swagger UI: <http://127.0.0.1:8000/docs>
- Scalar reference: <http://127.0.0.1:8000/reference> (loads its renderer from a CDN)
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>

Check readiness from a third terminal:

```powershell
curl.exe http://127.0.0.1:8000/health
curl.exe -s http://127.0.0.1:8000/ready
```

Expected readiness output includes:

```json
{
  "status": "ready",
  "portal_ok": true,
  "session_authenticated": true,
  "breaker": "closed"
}
```

If `/ready` is not `ready`, confirm the mock is running at <http://127.0.0.1:9000/health> first.

## 5. Try the API

```powershell
curl.exe -s "http://127.0.0.1:8000/api/v1/meters?page_size=2"
curl.exe -s "http://127.0.0.1:8000/api/v1/meters?status=FAULT"
curl.exe -s "http://127.0.0.1:8000/api/v1/meters/MTR-0011"
curl.exe -s "http://127.0.0.1:8000/api/v1/hierarchy"
curl.exe -s "http://127.0.0.1:8000/api/v1/stats/summary"
```

The mock dataset has 24 unique meters. The bulk export is the preferred source, so a healthy response reports `"source": "bulk_csv"`.

Refresh the cache with the development admin key:

```powershell
curl.exe -X POST -H "X-API-Key: dev-admin-key" http://127.0.0.1:8000/api/v1/admin/cache/refresh
```

The key is configurable through `ADMIN_API_KEY`; do not use the default outside local development.

## 6. Optional configuration

Defaults work with the mock. To customize:

```powershell
Copy-Item .env.example .env
```

Then edit `.env`, for example:

```dotenv
URJA_BASE_URL=http://127.0.0.1:9000
URJA_USERNAME=operator@urja.example
URJA_PASSWORD=urja-demo-pass
ADMIN_API_KEY=dev-admin-key
```

To point at a real portal, change `URJA_BASE_URL`, `URJA_USERNAME`, and `URJA_PASSWORD`. Do not commit `.env`. Real-portal behavior is still unverified; see [`PROTOCOL.md`](PROTOCOL.md).

## 7. Run the tests and checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\mypy.exe app mock_portal scripts
.\.venv\Scripts\python.exe scripts\export_openapi.py
```

Expected test result: `77 passed, 1 skipped`. The skipped test is the opt-in real-portal smoke test.

## 8. Stop everything

Press `Ctrl+C` in each terminal, or stop the listening processes:

```powershell
Get-NetTCPConnection -LocalPort 8000,9000 -State Listen |
  Select-Object LocalPort,OwningProcess

Stop-Process -Id <pid> -Force
```

## Docker alternative

Requires Docker Desktop or another Docker engine:

```powershell
docker compose up --build
```

Then open <http://127.0.0.1:8000>. Stop with:

```powershell
docker compose down
```

The compose file starts both the mock and API containers, with health checks. `docker compose config` validates in this repository; the image build was not executed in this environment because no Docker engine was available.

## macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# terminal 1
uvicorn mock_portal.app:app --host 127.0.0.1 --port 9000

# terminal 2
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Troubleshooting

**`ModuleNotFoundError: No module named 'uvicorn'`**

The virtual environment is not being used. Use the full `.\.venv\Scripts\python.exe -m ...` commands above, or activate the environment.

**`Activate.ps1 cannot be loaded`**

Skip activation and call `.\.venv\Scripts\python.exe` directly.

**Port 8000 or 9000 already in use**

Find and stop the process:

```powershell
Get-NetTCPConnection -LocalPort 8000,9000 -State Listen | Select-Object LocalPort,OwningProcess
Stop-Process -Id <pid> -Force
```

**`/ready` says `degraded` or the dashboard shows a portal error**

Start the mock before the API, then check:

```powershell
curl.exe http://127.0.0.1:9000/health
```

**`ZoneInfoNotFoundError` on Windows**

`tzdata` is a declared dependency; reinstall it with:

```powershell
.\.venv\Scripts\python.exe -m pip install tzdata
```

**Data looks stale**

The API intentionally serves stale data during upstream failures. The console shows `X-Cache` age; use the admin cache-refresh request or restart the API.
