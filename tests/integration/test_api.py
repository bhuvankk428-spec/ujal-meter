from __future__ import annotations

from fastapi.testclient import TestClient

from mock_portal.app import portal_state


def test_health_and_readiness(api_client: TestClient) -> None:
    health = api_client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "Urja Meter Ops API"

    ready = api_client.get("/ready")
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] == "ready"
    assert body["portal_ok"] is True
    assert body["session_authenticated"] is True
    assert portal_state.successful_logins == 1


def test_meter_list_filters_and_cache_headers(api_client: TestClient) -> None:
    first = api_client.get("/api/v1/meters", params={"page_size": 5})
    assert first.status_code == 200
    assert first.headers["x-cache"] == "MISS"
    body = first.json()
    assert body["page"] == 1
    assert body["page_size"] == 5
    assert body["total"] == 24
    assert body["next_page"] == 2
    assert len(body["items"]) == 5
    assert body["items"][0]["id"] == "MTR-0001"
    assert body["data_quality"] == {
        "flagged_meters": 14,
        "duplicate_ids": 1,
        "malformed_rows": 0,
        "missing_serial": 1,
        "missing_coordinates": 3,
        "unknown_status": 2,
    }
    assert body["source"]["source"] == "bulk_csv"

    second = api_client.get("/api/v1/meters", params={"page_size": 5})
    assert second.headers["x-cache"] == "HIT"

    fault = api_client.get("/api/v1/meters", params={"status": "FAULT"})
    assert fault.status_code == 200
    assert fault.json()["total"] == 2
    assert {"MTR-0004", "MTR-0018"} == {item["id"] for item in fault.json()["items"]}

    searched = api_client.get("/api/v1/meters", params={"q": "jayanagar"})
    assert searched.status_code == 200
    assert searched.json()["total"] == 4

    zoned = api_client.get("/api/v1/meters", params={"zone": "zn-n1"})
    assert zoned.status_code == 200
    assert zoned.json()["total"] == 5


def test_meter_detail_path_and_source_conflict(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/meters/MTR-0011")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "MTR-0011"
    assert body["serial_number"] == "SN-0011-REV"
    assert body["hierarchy_path"] == ["Bengaluru Network", "South", "Jayanagar"]
    assert "list_detail_field_conflict" in body["quality_flags"]
    assert body["source"]["source"] == "meter_page"
    assert response.headers["x-cache"] == "MISS"


def test_meter_not_found_problem_json(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/meters/MTR-9999")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 404
    assert body["instance"] == "/api/v1/meters/MTR-9999"
    assert body["request_id"]
    assert response.headers["x-request-id"] == body["request_id"]


def test_consumption_quality_flags_and_alias(api_client: TestClient) -> None:
    response = api_client.get(
        "/api/v1/meters/MTR-0014/consumption",
        params={
            "from": "2026-02-01T00:00:00Z",
            "to": "2026-02-10T00:00:00Z",
            "granularity": "daily",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "from" in body
    assert body["unit"] == "kWh"
    assert body["summary"]["missing_points"] >= 2
    assert "missing_values" in body["quality_flags"]
    assert any(point["value"] is None for point in body["points"])


def test_consumption_negative_and_duplicate_flags(api_client: TestClient) -> None:
    params = {
        "from": "2026-02-01T00:00:00Z",
        "to": "2026-02-10T00:00:00Z",
        "granularity": "daily",
    }
    negative = api_client.get("/api/v1/meters/MTR-0015/consumption", params=params).json()
    assert "negative_values" in negative["quality_flags"]
    assert negative["summary"]["minimum"] < 0

    duplicate = api_client.get("/api/v1/meters/MTR-0016/consumption", params=params).json()
    assert "duplicate_timestamps_dropped" in duplicate["quality_flags"]


def test_consumption_invalid_range_is_problem_json(api_client: TestClient) -> None:
    response = api_client.get(
        "/api/v1/meters/MTR-0001/consumption",
        params={
            "from": "2026-02-10T00:00:00Z",
            "to": "2026-02-01T00:00:00Z",
        },
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 422


def test_hierarchy_reports_defects(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/hierarchy")
    assert response.status_code == 200
    body = response.json()
    assert body["root"]["id"] == "__network__"
    assert body["data_quality"] == {
        "orphans": 1,
        "cycles": 1,
        "duplicates": 1,
        "multiple_parents": 1,
    }
    ids = {node["id"] for node in body["nodes"]}
    assert {"__unassigned__", "ZN-ORPHAN", "ZN-CYC-A", "ZN-CYC-B"} <= ids
    unassigned = next(node for node in body["nodes"] if node["id"] == "__unassigned__")
    assert unassigned["meter_count"] >= 1


def test_hierarchy_subtree_depth_and_unknown(api_client: TestClient) -> None:
    subtree = api_client.get("/api/v1/hierarchy", params={"root": "ZN-NORTH", "depth": 0})
    assert subtree.status_code == 200
    body = subtree.json()
    assert body["root"]["id"] == "ZN-NORTH"
    assert body["root"]["children"] == []
    assert body["root"]["has_more_children"] is True

    missing = api_client.get("/api/v1/hierarchy", params={"root": "ZN-NOPE"})
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")


def test_geo_search(api_client: TestClient) -> None:
    response = api_client.get(
        "/api/v1/meters/search",
        params={"lat": 12.98, "lon": 77.6, "radius_km": 10, "page_size": 100},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] > 0
    assert all(item["distance_km"] <= 10 for item in body["items"])
    assert all(item["latitude"] is not None for item in body["items"])
    distances = [item["distance_km"] for item in body["items"]]
    assert distances == sorted(distances)


def test_stats_summary(api_client: TestClient) -> None:
    body = api_client.get("/api/v1/stats/summary").json()
    assert body["total_meters"] == 24
    assert sum(body["status_counts"].values()) == 24
    assert body["with_coordinates"] == 21


def test_reauth_is_transparent_through_the_api(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/meters", params={"page_size": 1}).status_code == 200
    assert portal_state.successful_logins == 1

    portal_state.expire_all()
    response = api_client.get("/api/v1/meters/MTR-0002")
    assert response.status_code == 200
    assert response.json()["id"] == "MTR-0002"
    assert portal_state.successful_logins == 2


def test_admin_refresh_is_protected(api_client: TestClient) -> None:
    unauthorized = api_client.post("/api/v1/admin/cache/refresh")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["content-type"].startswith("application/problem+json")

    response = api_client.post(
        "/api/v1/admin/cache/refresh", headers={"X-API-Key": "test-admin-key"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meters_reloaded"] == 24
    assert body["hierarchy_reloaded"] >= 10


def test_cors_allows_local_console_only(api_client: TestClient) -> None:
    allowed = api_client.get("/health", headers={"Origin": "http://localhost:8000"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:8000"

    blocked = api_client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in blocked.headers
