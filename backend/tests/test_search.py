from collections import Counter
from uuid import uuid4


def _slug(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def _seed_search_entities(client, headers, source_db, slug: str):
    conn = client.post(
        "/api/v1/connections",
        json={"name": f"{slug}_connection", "dsn": source_db},
        headers=headers,
    )
    assert conn.status_code == 201, conn.text
    conn_id = conn.json()["id"]

    ds = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn_id, "tables": [{"table_name": f"{slug}_orders"}]},
        headers=headers,
    )
    assert ds.status_code == 201, ds.text
    dataset_id = ds.json()[0]["id"]

    check = client.post(
        "/api/v1/checks",
        json={
            "dataset_id": dataset_id,
            "name": f"{slug}_email_required",
            "check_type": "not_null",
            "column_name": "email",
        },
        headers=headers,
    )
    assert check.status_code == 201, check.text
    return conn_id, dataset_id, check.json()["id"]


def test_global_search_requires_auth(client):
    assert client.get("/api/v1/search?q=anything").status_code == 401


def test_global_search_matches_entities_case_insensitively(client, admin_headers, source_db):
    slug = _slug("global_search")
    _conn_id, dataset_id, check_id = _seed_search_entities(client, admin_headers, source_db, slug)

    resp = client.get(f"/api/v1/search?q={slug.upper()}&limit=5", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    hits = resp.json()["hits"]

    dataset_hit = next(h for h in hits if h["type"] == "dataset" and h["id"] == dataset_id)
    assert dataset_hit == {
        "type": "dataset",
        "id": dataset_id,
        "title": f"{slug}_orders",
        "subtitle": f"{slug}_connection",
        "url": f"/datasets/{dataset_id}",
    }

    check_hit = next(h for h in hits if h["type"] == "check" and h["id"] == check_id)
    assert check_hit["title"] == f"{slug}_email_required"
    assert check_hit["subtitle"] == f"{slug}_orders"
    assert check_hit["url"] == f"/datasets/{dataset_id}/checks"

    connection_hit = next(h for h in hits if h["type"] == "connection")
    assert connection_hit["title"] == f"{slug}_connection"
    assert connection_hit["subtitle"] == "sqlite"
    assert connection_hit["url"] == "/connections"

    assert [h["type"] for h in hits[:3]] == ["dataset", "check", "connection"]


def test_global_search_limit_applies_per_type(client, admin_headers, source_db):
    slug = _slug("search_limit")
    conn = client.post(
        "/api/v1/connections",
        json={"name": f"{slug}_connection", "dsn": source_db},
        headers=admin_headers,
    )
    assert conn.status_code == 201, conn.text
    conn_id = conn.json()["id"]

    ds = client.post(
        "/api/v1/datasets/register",
        json={
            "connection_id": conn_id,
            "tables": [
                {"table_name": f"{slug}_orders"},
                {"table_name": f"{slug}_customers"},
            ],
        },
        headers=admin_headers,
    )
    assert ds.status_code == 201, ds.text
    for dataset in ds.json():
        check = client.post(
            "/api/v1/checks",
            json={
                "dataset_id": dataset["id"],
                "name": f"{slug}_check_{dataset['id']}",
                "check_type": "not_null",
                "column_name": "email",
            },
            headers=admin_headers,
        )
        assert check.status_code == 201, check.text

    resp = client.get(f"/api/v1/search?q={slug}&limit=1", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    counts = Counter(hit["type"] for hit in resp.json()["hits"])
    assert counts["dataset"] == 1
    assert counts["check"] == 1
    assert counts["connection"] == 1
    assert all(count <= 1 for count in counts.values())
