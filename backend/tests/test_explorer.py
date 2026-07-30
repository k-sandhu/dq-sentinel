"""The exploration agent (`app/llm/explorer.py`) — issue #293.

The explorer is the one agent loop that writes SQL against a *real* customer
source before anything is proposed, and until now it had no direct test at all.
Everything here runs on a scripted FakeProvider (no network) against the shared
sqlite source fixture, and pins the four properties the golden rules require:

* the loop is **bounded** (`llm_max_explore_turns` is a hard ceiling);
* every agent-authored query goes through ``guard_sql()`` — writes and
  host-file/network read functions are rejected, and the refusal is fed back to
  the model instead of blowing up the request;
* rows are bounded by ``agent_query_row_limit`` and PII columns are redacted
  before any row text reaches the prompt;
* malformed or missing model output degrades to "no insights", never an
  exception, and with no provider configured the caller still gets the
  actionable ``No LLM provider configured`` message it degrades on.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from app.connectors.sa import Connector
from app.llm import client as llm_client
from app.llm import explorer as explorer_mod
from app.llm.explorer import explore_dataset
from app.llm.providers import BaseProvider, LlmResponse, ToolCall

PROFILE = "rows=200 (stats from 200-row sample)\n- email (TEXT, string) null%=2.5 distinct=194"


class FakeProvider(BaseProvider):
    """Scripted provider. With ``repeat_last`` the final response is returned
    forever, which is how a model that never calls the final tool is simulated."""

    name = "fake"

    def __init__(self, scripted: list[LlmResponse], repeat_last: bool = False):
        super().__init__("fake-model")
        self.scripted = list(scripted)
        self.repeat_last = repeat_last
        self.calls = 0
        self.seen: list[tuple[str, list[dict[str, Any]], Any]] = []

    def _complete(self, system, history, tools, json_schema, max_tokens, use_mcp) -> LlmResponse:
        self.calls += 1
        self.seen.append((system, [dict(h) for h in history], tools))
        if len(self.scripted) == 1 and self.repeat_last:
            return self.scripted[0]
        return self.scripted.pop(0)


def _sql_call(call_id: str, sql: str, purpose: str = "probe") -> LlmResponse:
    return LlmResponse(
        text="",
        tool_calls=[ToolCall(call_id, "run_sql", {"sql": sql, "purpose": purpose})],
        stop_reason="tool_use",
    )


def _submit(call_id: str, payload: dict[str, Any]) -> LlmResponse:
    return LlmResponse(
        text="",
        tool_calls=[ToolCall(call_id, "submit_insights", payload)],
        stop_reason="tool_use",
    )


INSIGHT = {
    "title": "5 rows have no email",
    "detail": "COUNT(*) WHERE email IS NULL = 5 of 200",
    "risk": "high",
    "column": "email",
    "suggested_check_type": "not_null",
}


def _install(monkeypatch, fake: FakeProvider, *, max_turns: int = 6, row_limit: int = 200) -> None:
    monkeypatch.setattr(llm_client, "get_provider", lambda: fake)
    monkeypatch.setattr(
        explorer_mod,
        "get_settings",
        lambda: SimpleNamespace(llm_max_explore_turns=max_turns, agent_query_row_limit=row_limit),
    )


def _results(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in transcript if t["type"] == "result"]


# ------------------------------------------------------------------ happy path
def test_explorer_runs_sql_then_submits_insights(source_db, monkeypatch):
    fake = FakeProvider(
        [
            LlmResponse(
                text="Let me look at the null emails.",
                tool_calls=[
                    ToolCall(
                        "t1",
                        "run_sql",
                        {"sql": "SELECT COUNT(*) AS n FROM people WHERE email IS NULL",
                         "purpose": "count null emails"},
                    )
                ],
                stop_reason="tool_use",
            ),
            LlmResponse(
                text="",
                tool_calls=[ToolCall("t2", "get_table_code", {"table": "people"})],
                stop_reason="tool_use",
            ),
            _submit("t3", {"insights": [INSIGHT]}),
        ]
    )
    _install(monkeypatch, fake)

    out = explore_dataset(Connector(source_db), "people", PROFILE, knowledge=None)

    assert out["insights"] == [INSIGHT]
    assert out["queries_run"] == 1  # only run_sql counts, not get_table_code
    steps = [t["type"] for t in out["transcript"]]
    assert steps == ["text", "sql", "result", "tool", "result", "final"]
    assert "5" in _results(out["transcript"])[0]["content"]
    assert _results(out["transcript"])[0]["error"] is False
    # get_table_code reached the source: sqlite hands back the real CREATE TABLE.
    assert "CREATE TABLE people" in _results(out["transcript"])[1]["content"]

    # the loop advertises exactly the three tools and carries results back
    tool_names = {t["name"] for t in fake.seen[0][2]}
    assert tool_names == {"run_sql", "get_table_code", "submit_insights"}
    assert fake.seen[-1][1][-1]["role"] == "tool_results"


# --------------------------------------------------------------------- bounded
def test_explorer_loop_is_bounded_by_max_turns(source_db, monkeypatch):
    """A model that queries forever must be stopped by llm_max_explore_turns —
    every turn is a real query against a customer database."""
    fake = FakeProvider([_sql_call("loop", "SELECT COUNT(*) AS n FROM people")], repeat_last=True)
    _install(monkeypatch, fake, max_turns=3)

    out = explore_dataset(Connector(source_db), "people", PROFILE, knowledge=None)

    assert fake.calls == 3, "the loop ran more turns than llm_max_explore_turns"
    assert out["queries_run"] == 3
    assert out["insights"] == []  # never finished -> nothing claimed
    assert out["transcript"][-1]["content"].startswith("(agent reached its turn limit")


def test_explorer_bounds_rows_per_query(source_db, monkeypatch):
    """agent_query_row_limit is the second bound: an unaggregated SELECT must not
    drag the whole table into the prompt."""
    fake = FakeProvider(
        [_sql_call("t1", "SELECT id, age FROM people"), _submit("t2", {"insights": []})]
    )
    _install(monkeypatch, fake, row_limit=4)

    out = explore_dataset(Connector(source_db), "people", PROFILE, knowledge=None)

    body = _results(out["transcript"])[0]["content"]
    assert body.strip().endswith("(4 rows)"), body


# --------------------------------------------------------------- guard_sql gate
@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("DELETE FROM people", "Only SELECT / WITH queries are allowed"),
        # starts with WITH, so only the keyword denylist stops it
        ("WITH d AS (DELETE FROM people RETURNING id) SELECT * FROM d", "Keyword not allowed"),
        ("SELECT * FROM people; DROP TABLE people", "Multiple statements are not allowed"),
        # #281/#267: a read-side function that escapes to the host is still a
        # single SELECT with no write keyword — the function denylist is the gate.
        ("SELECT readfile('/etc/passwd') AS leak", "Function not allowed"),
        ("SELECT * FROM read_csv_auto('C:/secrets.csv')", "Function not allowed"),
    ],
)
def test_explorer_sql_goes_through_guard_sql(source_db, monkeypatch, sql, expected):
    fake = FakeProvider([_sql_call("bad", sql), _submit("t2", {"insights": []})])
    _install(monkeypatch, fake)

    out = explore_dataset(Connector(source_db), "people", PROFILE, knowledge=None)

    (result,) = _results(out["transcript"])
    assert result["error"] is True
    assert expected in result["content"], result["content"]
    # ...and the refusal is handed back to the model so it can adapt, rather
    # than aborting the exploration.
    fed_back = fake.seen[-1][1][-1]
    assert fed_back["role"] == "tool_results"
    assert fed_back["results"][0]["is_error"] is True
    # the source is untouched — the rejected statement never executed
    assert Connector(source_db).row_count("people") == 200


# ----------------------------------------------------------------- PII redaction
def test_explorer_redacts_pii_columns_before_the_prompt(source_db, monkeypatch):
    """Golden rule 8: columns listed in the dataset's knowledge pii_columns are
    redacted before any row text is sent to the model."""
    fake = FakeProvider(
        [
            _sql_call("t1", "SELECT id, email, status FROM people WHERE email IS NOT NULL"),
            _submit("t2", {"insights": []}),
        ]
    )
    _install(monkeypatch, fake, row_limit=5)

    out = explore_dataset(
        Connector(source_db), "people", PROFILE, knowledge={"pii_columns": ["EMAIL"]}
    )

    body = _results(out["transcript"])[0]["content"]
    assert "[REDACTED]" in body
    assert "@example.com" not in body  # no raw address survives, in any casing
    assert "active" in body or "inactive" in body  # non-PII columns still readable
    # what the model actually received matches the transcript
    sent_back = fake.seen[-1][1][-1]["results"][0]["content"]
    assert "@example.com" not in sent_back


def test_explorer_without_pii_knowledge_keeps_values(source_db, monkeypatch):
    fake = FakeProvider(
        [_sql_call("t1", "SELECT email FROM people WHERE id = 20"), _submit("t2", {"insights": []})]
    )
    _install(monkeypatch, fake)

    out = explore_dataset(Connector(source_db), "people", PROFILE, knowledge={"pii_columns": []})
    assert "user20@example.com" in _results(out["transcript"])[0]["content"]


# ------------------------------------------------------------ graceful degrading
def test_explorer_tolerates_a_final_call_with_no_insights(source_db, monkeypatch):
    fake = FakeProvider([_sql_call("t1", "SELECT COUNT(*) AS n FROM people"), _submit("t2", {})])
    _install(monkeypatch, fake)

    out = explore_dataset(Connector(source_db), "people", PROFILE, knowledge=None)
    assert out == {"insights": [], "queries_run": 1, "transcript": out["transcript"]}


def test_explorer_tolerates_a_model_that_only_talks(source_db, monkeypatch):
    """Prose with no tool call: the loop nudges twice, then gives up — no raise."""
    fake = FakeProvider([LlmResponse(text="I think it looks fine.", stop_reason="end")],
                        repeat_last=True)
    _install(monkeypatch, fake, max_turns=10)

    out = explore_dataset(Connector(source_db), "people", PROFILE, knowledge=None)

    assert out["insights"] == []
    assert out["queries_run"] == 0
    assert fake.calls == 3, "the nudge budget (2) must cap a non-tool-calling model"


def test_explorer_without_provider_raises_the_actionable_error(source_db, monkeypatch):
    """No key configured: the caller (POST /checks/generate) catches this and
    falls back to heuristics, so it must stay a plain, recognisable RuntimeError."""
    monkeypatch.setattr(llm_client, "get_provider", lambda: None)
    with pytest.raises(RuntimeError, match="No LLM provider configured"):
        explore_dataset(Connector(source_db), "people", PROFILE, knowledge=None)


# ------------------------------------------------------------------- end to end
def _dataset_with_profile(client, headers, source_db, unique_name) -> int:
    conn = client.post(
        "/api/v1/connections",
        json={"name": unique_name("explore-conn"), "dsn": source_db},
        headers=headers,
    )
    assert conn.status_code == 201, conn.text
    registered = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn.json()["id"], "tables": [{"table_name": "people"}]},
        headers=headers,
    )
    assert registered.status_code in (200, 201), registered.text
    ds_id = registered.json()[0]["id"]
    profiled = client.post(f"/api/v1/datasets/{ds_id}/profile", headers=headers)
    assert profiled.status_code == 200, profiled.text
    return ds_id


def test_generate_with_explore_persists_insights(
    client, admin_headers, source_db, unique_name, monkeypatch
):
    """The full wiring: /checks/generate?explore -> explorer loop -> stored
    exploration -> the insights appear in the check-gen prompt."""
    ds_id = _dataset_with_profile(client, admin_headers, source_db, unique_name)
    fake = FakeProvider(
        [
            _sql_call("t1", "SELECT COUNT(*) AS n FROM people WHERE email IS NULL", "nulls"),
            _submit("t2", {"insights": [INSIGHT]}),
            LlmResponse(
                text='{"checks": [{"name": "email present", "check_type": "not_null",'
                ' "column_name": "email", "params": {}, "severity": "error",'
                ' "rationale": "exploration found 5 null emails"}]}'
            ),
        ]
    )
    monkeypatch.setattr(llm_client, "get_provider", lambda: fake)
    monkeypatch.setattr("app.api.checks.llm_enabled", lambda: True)

    resp = client.post(
        "/api/v1/checks/generate",
        json={"dataset_id": ds_id, "use_llm": True, "explore": True},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "llm"
    assert body["explored"] is True
    assert [c["check_type"] for c in body["checks"]] == ["not_null"]

    stored = client.get(f"/api/v1/datasets/{ds_id}/exploration", headers=admin_headers).json()
    assert stored["queries_run"] == 1
    assert stored["insights"] == [INSIGHT]

    # the check-gen turn (the last provider call) was given the exploration
    check_gen_prompt = fake.seen[-1][1][0]["text"]
    assert "Exploration insights" in check_gen_prompt
    assert INSIGHT["title"] in check_gen_prompt


def test_generate_with_explore_degrades_without_llm(
    client, admin_headers, source_db, unique_name
):
    """No provider configured: explore is skipped entirely and generation falls
    back to heuristics — the endpoint never 503s or 500s (golden rule 4)."""
    ds_id = _dataset_with_profile(client, admin_headers, source_db, unique_name)

    resp = client.post(
        "/api/v1/checks/generate",
        json={"dataset_id": ds_id, "use_llm": True, "explore": True},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "heuristic"
    assert resp.json()["explored"] is False
    assert resp.json()["created"] > 0

    stored = client.get(f"/api/v1/datasets/{ds_id}/exploration", headers=admin_headers).json()
    assert stored == {"insights": [], "queries_run": 0}
