"""Assistant chat: session CRUD/ownership, and the WebSocket turn loop end to
end on a scripted FakeProvider (tool calls -> steps -> chart -> persisted
messages). No network."""

import pytest
from starlette.websockets import WebSocketDisconnect

from app.llm import client as llm_client
from app.llm.chat_agent import drop_history
from app.llm.providers import BaseProvider, LlmResponse, ToolCall


class FakeProvider(BaseProvider):
    name = "fake"

    def __init__(self, scripted: list[LlmResponse]):
        super().__init__("fake-model")
        self.scripted = list(scripted)
        self.seen: list[tuple[str, list]] = []

    def _complete(self, system, history, tools, json_schema, max_tokens, use_mcp) -> LlmResponse:
        self.seen.append((system, [dict(h) for h in history]))
        return self.scripted.pop(0)


def _login(client, email, password) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _make_user(client, admin_headers, email, role) -> str:
    resp = client.post(
        "/api/v1/auth/users",
        json={"email": email, "password": "longenough1", "role": role},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return _login(client, email, "longenough1")


def _collect_until_done(ws) -> list[dict]:
    events = []
    while True:
        evt = ws.receive_json()
        events.append(evt)
        if evt["type"] == "done":
            return events


def _setup_connection(client, headers, source_db, name) -> int:
    resp = client.post("/api/v1/connections", json={"name": name, "dsn": source_db}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ----------------------------------------------------------------- REST
def test_session_crud_and_ownership(client, admin_headers):
    created = client.post("/api/v1/chat/sessions", json={}, headers=admin_headers)
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    assert created.json()["title"] == ""

    listed = client.get("/api/v1/chat/sessions", headers=admin_headers)
    assert sid in [s["id"] for s in listed.json()]

    detail = client.get(f"/api/v1/chat/sessions/{sid}", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.json()["messages"] == []

    # another (non-admin) user can neither read nor delete it
    other = _make_user(client, admin_headers, "chat-editor@example.com", "editor")
    other_headers = {"Authorization": f"Bearer {other}"}
    assert client.get(f"/api/v1/chat/sessions/{sid}", headers=other_headers).status_code == 403
    assert client.delete(f"/api/v1/chat/sessions/{sid}", headers=other_headers).status_code == 403
    assert listed.json() != client.get("/api/v1/chat/sessions", headers=other_headers).json()

    assert client.delete(f"/api/v1/chat/sessions/{sid}", headers=admin_headers).status_code == 204
    assert client.get(f"/api/v1/chat/sessions/{sid}", headers=admin_headers).status_code == 404

    assert client.get("/api/v1/chat/sessions").status_code in (401, 403)


# ------------------------------------------------------------ WebSocket
def test_ws_rejects_bad_token(client, admin_headers):
    sid = client.post("/api/v1/chat/sessions", json={}, headers=admin_headers).json()["id"]
    with client.websocket_connect(f"/api/v1/chat/ws/{sid}?token=not-a-jwt") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4401


def test_ws_requires_editor(client, admin_headers):
    sid = client.post("/api/v1/chat/sessions", json={}, headers=admin_headers).json()["id"]
    viewer_token = _make_user(client, admin_headers, "chat-viewer@example.com", "viewer")
    with client.websocket_connect(f"/api/v1/chat/ws/{sid}?token={viewer_token}") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4403


def test_ws_other_users_session_not_found(client, admin_headers):
    sid = client.post("/api/v1/chat/sessions", json={}, headers=admin_headers).json()["id"]
    editor_token = _make_user(client, admin_headers, "chat-editor2@example.com", "editor")
    with client.websocket_connect(f"/api/v1/chat/ws/{sid}?token={editor_token}") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4404


def test_ws_turn_with_sql_and_chart(client, admin_headers, source_db, monkeypatch):
    conn_id = _setup_connection(client, admin_headers, source_db, "chat-conn")
    token = _login(client, "admin@example.com", "admin123")
    sid = client.post("/api/v1/chat/sessions", json={}, headers=admin_headers).json()["id"]
    drop_history(sid)

    fake = FakeProvider(
        [
            LlmResponse(
                text="Let me count the rows first.",
                tool_calls=[
                    ToolCall(
                        "t1",
                        "run_sql",
                        {
                            "connection_id": conn_id,
                            "sql": "SELECT COUNT(*) AS n FROM people",
                            "purpose": "row count",
                        },
                    )
                ],
                stop_reason="tool_use",
            ),
            LlmResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        "t2",
                        "render_chart",
                        {
                            "connection_id": conn_id,
                            "sql": "SELECT status, COUNT(*) AS n FROM people GROUP BY 1",
                            "title": "Rows by status",
                            "chart_type": "bar",
                            "x": "status",
                            "y": "n",
                        },
                    )
                ],
                stop_reason="tool_use",
            ),
            LlmResponse(text="There are **200 rows**; see the chart for the status split."),
        ]
    )
    monkeypatch.setattr(llm_client, "get_provider", lambda: fake)

    with client.websocket_connect(f"/api/v1/chat/ws/{sid}?token={token}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "session"
        assert hello["session"]["id"] == sid
        assert hello["messages"] == []

        ws.send_json({"type": "user_message", "content": "How many rows are in people, by status?"})
        events = _collect_until_done(ws)

    by_type: dict[str, list] = {}
    for e in events:
        by_type.setdefault(e["type"], []).append(e)

    assert by_type["message_saved"][0]["message"]["role"] == "user"
    assert "error" not in by_type, by_type.get("error")

    steps = [e["step"] for e in by_type["step"]]
    sql_steps = [s for s in steps if s["type"] == "sql"]
    assert sql_steps and sql_steps[0]["purpose"] == "row count"
    result_steps = [s for s in steps if s["type"] == "result"]
    assert any("200" in s["content"] for s in result_steps)
    chart_steps = [s for s in steps if s["type"] == "chart"]
    assert len(chart_steps) == 1
    assert chart_steps[0]["title"] == "Rows by status"
    assert chart_steps[0]["columns"] == ["status", "n"]
    assert chart_steps[0]["viz"] == {"type": "bar", "x": "status", "y": "n"}
    assert len(chart_steps[0]["rows"]) >= 2

    final = by_type["assistant_message"][0]["message"]
    assert final["role"] == "assistant"
    assert "200 rows" in final["content"]
    assert [s["type"] for s in final["steps"]].count("chart") == 1

    # the model saw the platform context and the tool results
    system_prompt = fake.seen[0][0]
    assert "Current platform state" in system_prompt
    last_history = fake.seen[-1][1]
    assert last_history[-1]["role"] == "tool_results"

    # persisted: 1 user + 1 assistant message, session titled from the question
    detail = client.get(f"/api/v1/chat/sessions/{sid}", headers=admin_headers).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["title"].startswith("How many rows")
    assert detail["messages"][1]["steps"][-1]["type"] == "text"


def test_ws_sql_error_fed_back_to_model(client, admin_headers, source_db, monkeypatch):
    conn_id = _setup_connection(client, admin_headers, source_db, "chat-conn-err")
    token = _login(client, "admin@example.com", "admin123")
    sid = client.post("/api/v1/chat/sessions", json={}, headers=admin_headers).json()["id"]
    drop_history(sid)

    fake = FakeProvider(
        [
            LlmResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        "t1",
                        "run_sql",
                        {"connection_id": conn_id, "sql": "DELETE FROM people", "purpose": "oops"},
                    )
                ],
                stop_reason="tool_use",
            ),
            LlmResponse(text="I cannot modify data — reads only."),
        ]
    )
    monkeypatch.setattr(llm_client, "get_provider", lambda: fake)

    with client.websocket_connect(f"/api/v1/chat/ws/{sid}?token={token}") as ws:
        ws.receive_json()  # session hello
        ws.send_json({"type": "user_message", "content": "Delete everything"})
        events = _collect_until_done(ws)

    error_results = [
        e["step"] for e in events if e["type"] == "step" and e["step"]["type"] == "result"
    ]
    assert error_results and error_results[0]["error"] is True
    # the failed call's result was fed back to the model, not fatal
    last_history = fake.seen[-1][1]
    assert last_history[-1]["results"][0]["is_error"] is True
    final = [e for e in events if e["type"] == "assistant_message"][0]["message"]
    assert "reads only" in final["content"]


def test_ws_without_provider_degrades(client, admin_headers):
    """No LLM configured -> error event + persisted explanatory message, socket stays sane."""
    token = _login(client, "admin@example.com", "admin123")
    sid = client.post("/api/v1/chat/sessions", json={}, headers=admin_headers).json()["id"]
    drop_history(sid)

    with client.websocket_connect(f"/api/v1/chat/ws/{sid}?token={token}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": "hello?"})
        events = _collect_until_done(ws)

    assert any(e["type"] == "error" for e in events)
    final = [e for e in events if e["type"] == "assistant_message"][0]["message"]
    assert "No LLM provider configured" in final["content"]

    detail = client.get(f"/api/v1/chat/sessions/{sid}", headers=admin_headers).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
