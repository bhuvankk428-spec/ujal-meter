from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from app.portal.exceptions import ParseError
from app.portal.parsers import (
    parse_consumption_json,
    parse_hierarchy,
    parse_login_page,
    parse_meter_detail,
    parse_meter_list,
    parse_meters_csv,
)


def test_login_form_parsing(fixture_text: Callable[[str], str]) -> None:
    form = parse_login_page(fixture_text("login.html"))
    assert form["action"] == "/login"
    assert form["email_field"] == "email"
    assert form["password_field"] == "password"
    assert form["csrf_token"] == "fixture-csrf-token"


def test_csv_parsing(fixture_text: Callable[[str], str]) -> None:
    rows = parse_meters_csv(fixture_text("meters.csv"))
    assert len(rows) == 25
    assert {row["meter_id"] for row in rows if row["meter_id"].strip() == "MTR-0004"} == {
        "MTR-0004"
    }
    assert rows[0]["display_name"] == "Gandhi Nagar Feeder"
    assert rows[0]["last_seen"] == "2026-03-01T10:15:00+05:30"


def test_html_list_parsing(fixture_text: Callable[[str], str]) -> None:
    parsed = parse_meter_list(fixture_text("meters_list_page1.html"))
    assert parsed["page"] == 1
    assert parsed["total"] == 25
    assert parsed["next_page"] == 2
    assert len(parsed["rows"]) == 10
    assert parsed["rows"][0]["meter_id"] == "MTR-0001"
    assert parsed["rows"][0]["zone_id"] == "ZN-N1"


def test_column_order_does_not_matter() -> None:
    html = """
    <html><body>
    <table id="meters-table">
      <thead><tr><th>Status</th><th>Name</th><th>Meter ID</th><th>Serial No</th>
      <th>Zone</th><th>Zone Name</th><th>Latitude</th><th>Longitude</th>
      <th>Installed On</th><th>Last Seen</th><th>Last Reading</th>
      <th>Last Reading Unit</th><th>Type</th></tr></thead>
      <tbody><tr><td>Active</td><td>Test</td><td>MTR-1</td><td>SN-1</td>
      <td>ZN-1</td><td>Zone</td><td>1</td><td>2</td><td>2026-01-01</td>
      <td>2026-01-02</td><td>10</td><td>kWh</td><td>Smart</td></tr></tbody>
    </table>
    <div id="pager" data-page="1" data-total="1" data-next=""></div>
    </body></html>
    """
    parsed = parse_meter_list(html)
    assert parsed["rows"][0]["meter_id"] == "MTR-1"
    assert parsed["rows"][0]["display_name"] == "Test"
    assert parsed["rows"][0]["latitude"] == "1"
    assert parsed["next_page"] is None


def test_meter_detail_parsing(fixture_text: Callable[[str], str]) -> None:
    detail = parse_meter_detail(fixture_text("meter_detail.html"))
    assert detail["meter_id"] == "MTR-0001"
    assert detail["serial_no"] == "SN-0001"
    assert detail["address"].startswith("1 Gandhi Nagar Feeder Road")


def test_hierarchy_parsing(fixture_text: Callable[[str], str]) -> None:
    rows = parse_hierarchy(fixture_text("hierarchy.html"))
    assert len(rows) == 13
    assert {"node_id", "parent_id", "name", "type", "level"} == set(rows[0])
    assert any(row["parent_id"] == "" for row in rows)


def test_consumption_json_parsing(fixture_text: Callable[[str], str]) -> None:
    payload = parse_consumption_json(fixture_text("consumption.json"))
    assert payload["meter_id"] == "MTR-0014"
    assert payload["unit"] == "kWh"
    assert len(payload["points"]) == 7
    assert payload["points"][2]["value"] is None
    assert payload["points"][4]["value"] == "-"


@pytest.mark.parametrize(
    ("payload", "parser"),
    [
        ("<html>no table</html>", lambda text: parse_meter_list(text)),
        ("<html>no detail</html>", lambda text: parse_meter_detail(text)),
        ("<html>no table</html>", lambda text: parse_hierarchy(text)),
        ("a,b\n1,2\n", lambda text: parse_meters_csv(text)),
        ('{"points": "not-a-list"}', lambda text: parse_consumption_json(text)),
        ("<html>no form</html>", lambda text: parse_login_page(text)),
    ],
)
def test_structural_changes_raise(payload: str, parser: Callable[[str], object]) -> None:
    with pytest.raises(ParseError):
        parser(payload)


def test_missing_required_column_raises() -> None:
    with pytest.raises(ParseError, match="required"):
        parse_meters_csv("meter_id,status\nMTR-1,Active\n")


def test_consumption_json_extracts_payload() -> None:
    payload = parse_consumption_json(
        json.dumps(
            {
                "meter_id": "MTR-1",
                "unit": "kWh",
                "granularity": "daily",
                "points": [{"timestamp": "2026-01-01T00:00:00Z", "value": "1"}],
            }
        )
    )
    assert payload["points"][0]["value"] == "1"
