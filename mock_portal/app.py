"""Mock Urja Meter Ops portal.

Run:  uvicorn mock_portal.app:app --port 9000

It intentionally reproduces the awkward parts of a legacy session app:
server-rendered tables, a CSRF-protected form login, three different expiry
signals, and inconsistent source data.
"""

from __future__ import annotations

import html
import math
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import FastAPI, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from mock_portal import data

SESSION_COOKIE = "urja_session"
CSRF_COOKIE = "urja_csrf"
PAGE_SIZE = 10
SESSION_TTL_S = int(os.getenv("MOCK_SESSION_TTL_SECONDS", "900"))
CONTROL_ENABLED = os.getenv("MOCK_CONTROL_ENABLED", "true").lower() in {"1", "true", "yes"}
USERNAME = os.getenv("URJA_USERNAME", "operator@urja.example")
PASSWORD = os.getenv("URJA_PASSWORD", "urja-demo-pass")


class PortalState:
    def __init__(self) -> None:
        self.sessions: dict[str, float] = {}
        self.login_attempts = 0
        self.successful_logins = 0
        self.failed_logins = 0

    def create_session(self) -> str:
        token = secrets.token_urlsafe(24)
        self.sessions[token] = time.time() + SESSION_TTL_S
        return token

    def validate(self, token: str | None) -> bool:
        if not token:
            return False
        expiry = self.sessions.get(token)
        if expiry is None:
            return False
        if expiry < time.time():
            self.sessions.pop(token, None)
            return False
        return True

    def expire_all(self) -> int:
        count = len(self.sessions)
        self.sessions.clear()
        return count

    def stats(self) -> dict[str, object]:
        return {
            "sessions": len(self.sessions),
            "login_attempts": self.login_attempts,
            "successful_logins": self.successful_logins,
            "failed_logins": self.failed_logins,
            "session_ttl_seconds": SESSION_TTL_S,
        }


portal_state = PortalState()
app = FastAPI(title="Urja Meter Ops (mock)", docs_url=None, redoc_url=None)


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "urja-meter-ops-mock",
        "sessions": len(portal_state.sessions),
    }


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/meters", status_code=303)


@app.get("/robots.txt", include_in_schema=False)
async def robots() -> Response:
    return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request, error: str | None = None) -> HTMLResponse:
    return _login_response(error=error)


@app.post("/login", include_in_schema=False)
async def login_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    portal_state.login_attempts += 1
    cookie_token = request.cookies.get(CSRF_COOKIE)
    if not cookie_token or not secrets.compare_digest(cookie_token, csrf_token):
        portal_state.failed_logins += 1
        return _login_response(error="Your sign-in page expired. Please try again.")

    if not (secrets.compare_digest(email, USERNAME) and secrets.compare_digest(password, PASSWORD)):
        portal_state.failed_logins += 1
        return _login_response(error="Invalid email or password.")

    portal_state.successful_logins += 1
    token = portal_state.create_session()
    response = RedirectResponse("/meters", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_S,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/logout", include_in_schema=False)
async def logout(request: Request) -> RedirectResponse:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        portal_state.sessions.pop(token, None)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/meters", response_class=HTMLResponse, include_in_schema=False)
async def meters_page(
    request: Request,
    page: int = Query(1, ge=1),
    q: str | None = None,
    status: str | None = None,
) -> Response:
    if not _authorized(request):
        return _login_response(error="Your session has expired. Please sign in again.")

    rows = data.csv_rows()
    if q:
        needle = q.casefold()
        rows = [
            row
            for row in rows
            if needle in (row["meter_id"] + row["serial_no"] + row["display_name"]).casefold()
        ]
    if status:
        rows = [row for row in rows if row["status"].casefold() == status.casefold()]

    total = len(rows)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, page), pages)
    start = (page - 1) * PAGE_SIZE
    page_rows = rows[start : start + PAGE_SIZE]
    next_page = page + 1 if page < pages else None

    body_rows = []
    for row in page_rows:
        body_rows.append(
            "<tr>"
            f"<td>{html.escape(row['meter_id'])}</td>"
            f"<td>{html.escape(row['serial_no'])}</td>"
            f"<td>{html.escape(row['display_name'])}</td>"
            f"<td>{html.escape(row['zone_id'])}</td>"
            f"<td>{html.escape(row['status'])}</td>"
            f"<td>{html.escape(row['last_seen'])}</td>"
            "</tr>"
        )

    html_body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Meters - Urja Meter Ops</title></head>
<body>
<header><h1>Urja Meter Ops</h1><p>Signed in as the shared operations account.</p></header>
<main>
<h2>Meters</h2>
<table id="meters-table">
<thead><tr><th>Meter ID</th><th>Serial No</th><th>Name</th><th>Zone</th><th>Status</th><th>Last Seen</th></tr></thead>
<tbody>{"".join(body_rows)}</tbody>
</table>
<div id="pager" data-page="{page}" data-total="{total}" data-next="{next_page or ""}">
  Page {page} of {pages} · {total} meters
</div>
<p><a href="/export/meters.csv">Download all meters as CSV</a></p>
</main>
</body></html>"""
    return HTMLResponse(html_body)


@app.get("/meters/{meter_id}", response_class=HTMLResponse, include_in_schema=False)
async def meter_detail(request: Request, meter_id: str) -> Response:
    if not _authorized(request):
        return _login_response(error="Your session has expired. Please sign in again.")

    canonical = meter_id.strip().upper()
    record = data.METERS_BY_ID.get(canonical)
    if record is None:
        return HTMLResponse("<h1>Meter not found</h1>", status_code=404)

    fields = [
        ("display_name", record["display_name"]),
        ("serial_no", record["serial_no"]),
        ("status", record["status"]),
        ("zone_id", record["zone_id"]),
        ("zone_name", record["zone_name"]),
        ("latitude", record["latitude"]),
        ("longitude", record["longitude"]),
        ("installed_on", record["installed_on"]),
        ("last_seen", record["last_seen"]),
        ("last_reading", record["last_reading"]),
        ("last_reading_unit", record["last_reading_unit"]),
        ("meter_type", record["meter_type"]),
        ("address", record["address"]),
        ("phase", record["phase"]),
        ("multiplier", record["multiplier"]),
    ]
    blocks = "".join(
        f'<div data-field="{name}"><dt>{html.escape(name)}</dt><dd>{html.escape(value)}</dd></div>'
        for name, value in fields
    )
    html_body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(canonical)} - Urja Meter Ops</title></head>
<body>
<main>
<h1>Meter {html.escape(canonical)}</h1>
<section id="meter-detail" data-meter-id="{html.escape(canonical)}">
<dl>{blocks}</dl>
</section>
<p><a href="/meters/{html.escape(canonical)}/consumption?from=2026-02-01&amp;to=2026-03-01&amp;granularity=daily">Consumption</a></p>
<p><a href="/meters">Back to meters</a></p>
</main>
</body></html>"""
    return HTMLResponse(html_body)


@app.get("/meters/{meter_id}/consumption/data", include_in_schema=False)
async def consumption_data(
    request: Request,
    meter_id: str,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    granularity: str = "daily",
) -> Response:
    if not _authorized(request):
        return JSONResponse({"error": "session_expired"}, status_code=401)

    canonical = meter_id.strip().upper()
    if canonical not in data.METERS_BY_ID:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if granularity not in {"hourly", "daily", "monthly"}:
        return JSONResponse({"error": "bad_granularity"}, status_code=400)

    now = datetime.now(UTC)
    try:
        end = data.parse_portal_datetime(to) if to else now
        start = data.parse_portal_datetime(from_) if from_ else end - timedelta(days=7)
    except ValueError:
        return JSONResponse({"error": "bad_datetime"}, status_code=400)

    payload = data.consumption_payload(canonical, from_=start, to=end, granularity=granularity)
    return JSONResponse(payload)


@app.get("/hierarchy", response_class=HTMLResponse, include_in_schema=False)
async def hierarchy_page(request: Request) -> Response:
    if not _authorized(request):
        return _login_response(error="Your session has expired. Please sign in again.")

    rows = "".join(
        "<tr>"
        f"<td>{html.escape(row['node_id'])}</td>"
        f"<td>{html.escape(row['parent_id'])}</td>"
        f"<td>{html.escape(row['name'])}</td>"
        f"<td>{html.escape(row['type'])}</td>"
        f"<td>{html.escape(row['level'])}</td>"
        "</tr>"
        for row in data.hierarchy_rows()
    )
    html_body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Hierarchy - Urja Meter Ops</title></head>
<body>
<main><h1>Network hierarchy</h1>
<table id="hierarchy-table">
<thead><tr><th>Node ID</th><th>Parent ID</th><th>Name</th><th>Type</th><th>Level</th></tr></thead>
<tbody>{rows}</tbody>
</table></main>
</body></html>"""
    return HTMLResponse(html_body)


@app.get("/export/meters.csv", include_in_schema=False)
async def export_meters(request: Request) -> Response:
    if not _authorized(request):
        return RedirectResponse("/login", status_code=303)
    return Response(
        content=data.meters_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="meters.csv"'},
    )


@app.post("/_control/expire", include_in_schema=False)
async def control_expire() -> Response:
    if not CONTROL_ENABLED:
        return JSONResponse({"error": "not_found"}, status_code=404)
    expired = portal_state.expire_all()
    return JSONResponse({"expired_sessions": expired, **portal_state.stats()})


@app.get("/_control/stats", include_in_schema=False)
async def control_stats() -> Response:
    if not CONTROL_ENABLED:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(portal_state.stats())


@app.post("/_control/reset", include_in_schema=False)
async def control_reset() -> Response:
    if not CONTROL_ENABLED:
        return JSONResponse({"error": "not_found"}, status_code=404)
    portal_state.sessions.clear()
    portal_state.login_attempts = 0
    portal_state.successful_logins = 0
    portal_state.failed_logins = 0
    return JSONResponse(portal_state.stats())


def _authorized(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if portal_state.validate(token):
        return True
    if token:
        portal_state.sessions.pop(token, None)
    return False


def _login_response(error: str | None = None) -> HTMLResponse:
    csrf = secrets.token_urlsafe(18)
    error_block = f'<div class="error" role="alert">{html.escape(error)}</div>' if error else ""
    html_body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Sign in to Urja Meter Ops</title></head>
<body>
<main id="login-panel">
<h1>Sign in to Urja Meter Ops</h1>
{error_block}
<form id="login-form" method="post" action="/login">
  <input type="hidden" name="csrf_token" value="{html.escape(csrf)}">
  <label>Email <input type="email" name="email" autocomplete="username"></label>
  <label>Password <input type="password" name="password" autocomplete="current-password"></label>
  <button type="submit">Sign in</button>
</form>
</main>
</body></html>"""
    response = HTMLResponse(html_body)
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=600,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
