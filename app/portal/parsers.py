"""Fetch-shaped responses -> raw dicts.

Parsers never coerce values: they return strings exactly as the portal sent
them. Normalization is intentionally a separate step (see normalize.py), so a
unit test can prove which layer loses information.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from selectolax.parser import HTMLParser, Node

from app.portal.exceptions import ParseError

_WS_RE = re.compile(r"[\s\u00a0]+")


def clean_text(node: Node | None) -> str:
    if node is None:
        return ""
    return _WS_RE.sub(" ", node.text(separator=" ", strip=True)).strip()


def header_key(value: str) -> str:
    """'Meter ID' / 'meter-id' / 'METER_ID' all become 'meter_id'."""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


class _Table:
    def __init__(self, html: str, table_id: str) -> None:
        tree = HTMLParser(html)
        table = tree.css_first(f"table#{table_id}")
        if table is None:
            raise ParseError(f'expected table id="{table_id}" was not found')
        header_cells = table.css("thead th") or table.css("tr:first-child th")
        if not header_cells:
            raise ParseError(f'table id="{table_id}" has no header row')
        self.headers = [header_key(clean_text(cell)) for cell in header_cells]
        if len(set(self.headers)) != len(self.headers):
            raise ParseError(f'table id="{table_id}" has duplicate headers: {self.headers}')
        self.rows = table.css("tbody tr") or table.css("tr")[1:]
        self._index = {name: i for i, name in enumerate(self.headers)}

    def require(self, aliases: dict[str, set[str]]) -> None:
        missing = [field for field, names in aliases.items() if not names.intersection(self._index)]
        if missing:
            raise ParseError(f"table is missing required headers {missing}; found {self.headers}")

    def value(self, row: Node, field: str, aliases: dict[str, set[str]]) -> str:
        for name in aliases[field]:
            if name in self._index:
                cells = row.css("td")
                idx = self._index[name]
                if idx >= len(cells):
                    return ""
                return clean_text(cells[idx])
        return ""


METER_LIST_ALIASES: dict[str, set[str]] = {
    "meter_id": {"meter_id", "meter", "id"},
    "serial_no": {"serial_no", "serial", "serial_number"},
    "display_name": {"display_name", "name", "meter_name"},
    "status": {"status", "state"},
    "zone_id": {"zone_id", "zone", "parent_id"},
    "zone_name": {"zone_name", "zone_label"},
    "latitude": {"latitude", "lat"},
    "longitude": {"longitude", "lon", "lng"},
    "installed_on": {"installed_on", "install_date", "installed"},
    "last_seen": {"last_seen", "last_seen_at", "last_comm"},
    "last_reading": {"last_reading", "reading"},
    "last_reading_unit": {"last_reading_unit", "unit"},
    "meter_type": {"meter_type", "type"},
}


LIST_REQUIRED_ALIASES = {
    field: METER_LIST_ALIASES[field] for field in ("meter_id", "display_name", "status")
}


def parse_meter_list(html: str) -> dict[str, Any]:
    table = _Table(html, "meters-table")
    table.require(LIST_REQUIRED_ALIASES)
    tree = HTMLParser(html)
    pager = tree.css_first("#pager")
    page = _int_attr(pager, "data-page")
    total = _int_attr(pager, "data-total")
    next_page = _int_attr(pager, "data-next")

    rows: list[dict[str, str]] = []
    for row in table.rows:
        rows.append(
            {
                "meter_id": table.value(row, "meter_id", METER_LIST_ALIASES),
                "serial_no": table.value(row, "serial_no", METER_LIST_ALIASES),
                "display_name": table.value(row, "display_name", METER_LIST_ALIASES),
                "status": table.value(row, "status", METER_LIST_ALIASES),
                "zone_id": table.value(row, "zone_id", METER_LIST_ALIASES),
                "zone_name": table.value(row, "zone_name", METER_LIST_ALIASES),
                "latitude": table.value(row, "latitude", METER_LIST_ALIASES),
                "longitude": table.value(row, "longitude", METER_LIST_ALIASES),
                "installed_on": table.value(row, "installed_on", METER_LIST_ALIASES),
                "last_seen": table.value(row, "last_seen", METER_LIST_ALIASES),
                "last_reading": table.value(row, "last_reading", METER_LIST_ALIASES),
                "last_reading_unit": table.value(row, "last_reading_unit", METER_LIST_ALIASES),
                "meter_type": table.value(row, "meter_type", METER_LIST_ALIASES),
            }
        )
    return {"rows": rows, "page": page, "total": total, "next_page": next_page}


def _int_attr(node: Node | None, attr: str) -> int | None:
    if node is None:
        return None
    raw = node.attributes.get(attr)
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw.strip())
    except ValueError:
        raise ParseError(f"expected integer {attr}={raw!r} on #pager") from None


def parse_meter_detail(html: str) -> dict[str, str]:
    tree = HTMLParser(html)
    section = tree.css_first("#meter-detail")
    if section is None:
        raise ParseError('expected section id="meter-detail" was not found')
    data: dict[str, str] = {}
    for key, value in section.attributes.items():
        if key.startswith("data-"):
            data[key[5:].replace("-", "_")] = value or ""
    if "meter_id" not in data:
        raise ParseError("meter detail section is missing data-meter-id")

    found_any = False
    for block in tree.css("#meter-detail [data-field]"):
        field = (block.attributes.get("data-field") or "").strip()
        if not field:
            continue
        found_any = True
        data[field] = clean_text(block.css_first("dd") or block)
    if not found_any:
        raise ParseError("meter detail page contained no data-field blocks")
    return data


def parse_hierarchy(html: str) -> list[dict[str, str]]:
    aliases: dict[str, set[str]] = {
        "node_id": {"node_id", "id", "zone_id"},
        "parent_id": {"parent_id", "parent"},
        "name": {"name", "label"},
        "type": {"type", "node_type"},
        "level": {"level", "depth"},
    }
    table = _Table(html, "hierarchy-table")
    table.require(aliases)
    rows: list[dict[str, str]] = []
    for row in table.rows:
        rows.append({field: table.value(row, field, aliases) for field in aliases})
    return rows


def parse_consumption_json(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"consumption response was not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ParseError("consumption JSON root must be an object")
    points = payload.get("points")
    if points is None or not isinstance(points, list):
        raise ParseError("consumption JSON is missing a points array")
    for i, point in enumerate(points):
        if not isinstance(point, dict) or "timestamp" not in point:
            raise ParseError(f"consumption point {i} is missing a timestamp")
    return payload


CSV_ALIASES: dict[str, set[str]] = {
    "meter_id": {"meter_id", "meter", "id"},
    "serial_no": {"serial_no", "serial", "serial_number"},
    "display_name": {"display_name", "name", "meter_name"},
    "status": {"status", "state"},
    "zone_id": {"zone_id", "zone", "parent_id"},
    "zone_name": {"zone_name", "zone_label"},
    "latitude": {"latitude", "lat"},
    "longitude": {"longitude", "lon", "lng"},
    "installed_on": {"installed_on", "install_date", "installed"},
    "last_seen": {"last_seen", "last_seen_at", "last_comm"},
    "last_reading": {"last_reading", "reading"},
    "last_reading_unit": {"last_reading_unit", "unit"},
    "meter_type": {"meter_type", "type"},
}


def parse_meters_csv(text: str) -> list[dict[str, str]]:
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ParseError("meters CSV has no header row")
    index = {header_key(name): name for name in reader.fieldnames}
    resolved: dict[str, str] = {}
    for field, aliases in CSV_ALIASES.items():
        for alias in aliases:
            if alias in index:
                resolved[field] = index[alias]
                break
        else:
            raise ParseError(f"meters CSV is missing required column {field!r}")

    rows: list[dict[str, str]] = []
    for raw in reader:
        rows.append({field: (raw.get(column) or "") for field, column in resolved.items()})
    if not rows:
        raise ParseError("meters CSV contained no data rows")
    return rows


def parse_login_page(html: str) -> dict[str, str]:
    tree = HTMLParser(html)
    form = tree.css_first("form#login-form")
    if form is None:
        raise ParseError('login form id="login-form" not found')
    token_node = form.css_first('input[name="csrf_token"]')
    if token_node is None:
        raise ParseError("login form has no csrf_token hidden input")
    email = form.css_first('input[name="email"]')
    password = form.css_first('input[name="password"]')
    if email is None or password is None:
        raise ParseError("login form is missing email/password inputs")
    return {
        "action": (form.attributes.get("action") or "/login").strip(),
        "csrf_token": (token_node.attributes.get("value") or "").strip(),
        "email_field": (email.attributes.get("name") or "").strip(),
        "password_field": (password.attributes.get("name") or "").strip(),
    }


LOGIN_MARKERS = ('id="login-form"', "Sign in to Urja Meter Ops")


def is_login_html(html: str) -> bool:
    return any(marker in html for marker in LOGIN_MARKERS)
