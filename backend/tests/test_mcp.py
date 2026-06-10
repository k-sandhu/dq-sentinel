"""MCP server registry: admin CRUD, write-only tokens, agent-call wiring."""

from app.llm.client import enabled_mcp_servers


def test_mcp_crud_and_token_masking(client, admin_headers):
    resp = client.post(
        "/api/v1/mcp-servers",
        json={
            "name": "dbt-models",
            "url": "https://mcp.example.com/sse",
            "auth_token": "secret-token-123",
            "description": "dbt project code",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    server = resp.json()
    assert server["has_token"] is True
    assert "secret-token-123" not in resp.text  # token is write-only

    # duplicate name rejected
    assert client.post(
        "/api/v1/mcp-servers",
        json={"name": "dbt-models", "url": "https://other.example.com"},
        headers=admin_headers,
    ).status_code == 409

    # bad URL rejected
    assert client.post(
        "/api/v1/mcp-servers",
        json={"name": "bad", "url": "ftp://nope"},
        headers=admin_headers,
    ).status_code == 422

    # enabled servers are what LLM calls will attach
    servers = enabled_mcp_servers()
    assert any(s["name"] == "dbt-models" and s["authorization_token"] == "secret-token-123" for s in servers)

    # disable -> excluded from LLM wiring
    resp = client.patch(
        f"/api/v1/mcp-servers/{server['id']}", json={"enabled": False}, headers=admin_headers
    )
    assert resp.json()["enabled"] is False
    assert not any(s["name"] == "dbt-models" for s in enabled_mcp_servers())

    assert client.delete(
        f"/api/v1/mcp-servers/{server['id']}", headers=admin_headers
    ).status_code == 204


def test_mcp_admin_only(client, admin_headers):
    client.post(
        "/api/v1/auth/users",
        json={"email": "mcp-editor@example.com", "password": "editor123", "role": "editor"},
        headers=admin_headers,
    )
    token = client.post(
        "/api/v1/auth/login", json={"email": "mcp-editor@example.com", "password": "editor123"}
    ).json()["access_token"]
    eh = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/mcp-servers", headers=eh).status_code == 200  # visible
    assert client.post(
        "/api/v1/mcp-servers", json={"name": "x", "url": "https://x.example.com"}, headers=eh
    ).status_code == 403  # but admin-gated for writes
