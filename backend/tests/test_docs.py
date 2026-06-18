"""In-app documentation browser (/api/v1/docs) — reads the repo docs/ folder."""


def test_list_docs(client, admin_headers):
    resp = client.get("/api/v1/docs", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    docs = resp.json()
    assert isinstance(docs, list) and len(docs) >= 1
    slugs = {d["slug"] for d in docs}
    # docs/competitive-analysis.md ships in the repo.
    assert "competitive-analysis" in slugs
    for d in docs:
        assert d["slug"] and d["title"]
        assert d["size_bytes"] > 0
        assert "markdown" not in d  # the list is summaries only


def test_get_doc(client, admin_headers):
    resp = client.get("/api/v1/docs/competitive-analysis", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slug"] == "competitive-analysis"
    assert body["title"]  # derived from the first H1
    assert body["markdown"].lstrip().startswith("#")


def test_docs_require_auth(client):
    assert client.get("/api/v1/docs").status_code == 401
    assert client.get("/api/v1/docs/competitive-analysis").status_code == 401


def test_unknown_doc_404(client, admin_headers):
    resp = client.get("/api/v1/docs/does-not-exist", headers=admin_headers)
    assert resp.status_code == 404


def test_doc_slug_traversal_blocked(client, admin_headers):
    # Single-segment slugs that must never resolve to a file outside docs/.
    for bad in ["..", "weird!name", "config"]:
        resp = client.get(f"/api/v1/docs/{bad}", headers=admin_headers)
        assert resp.status_code == 404, f"{bad} -> {resp.status_code}"
