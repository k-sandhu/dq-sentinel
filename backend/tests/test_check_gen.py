"""LLM check proposal parse/validate path (`app/llm/check_gen.py`) — issue #293.

`tests/test_generator.py` covers the *heuristic* proposals; the LLM path — the
one that turns free-form model output into rows in the analyst's proposal wall —
had no test. What matters here is that the model is never trusted:

* markdown-fenced JSON (what models actually emit) is parsed;
* every proposal is re-validated against the real check registry, and a bad one
  is DROPPED while its siblings survive — one hallucinated column must not cost
  the analyst the whole generation;
* a `custom_sql` proposal is SQL the platform will later run against a customer
  source, so it goes through `guard_sql()` like any other agent SQL;
* PII columns never reach the prompt;
* garbage output and a missing LLM key both degrade to heuristics at the API
  boundary instead of 500ing (golden rule 4).

Everything runs on a scripted FakeProvider — no network.
"""

import json
from typing import Any

import pytest

from app.core.check_types import CHECK_TYPES
from app.llm import client as llm_client
from app.llm.check_gen import CHECKS_SCHEMA, generate_checks_llm
from app.llm.providers import BaseProvider, LlmResponse

COLUMNS = {"id", "email", "age", "status", "score", "created_at"}
PROFILE = "rows=200 (stats from 200-row sample)\n- email (TEXT, string) null%=2.5 distinct=194"


class FakeProvider(BaseProvider):
    name = "fake"

    def __init__(self, scripted: list[LlmResponse]):
        super().__init__("fake-model")
        self.scripted = list(scripted)
        self.seen: list[dict[str, Any]] = []

    def _complete(self, system, history, tools, json_schema, max_tokens, use_mcp) -> LlmResponse:
        self.seen.append(
            {
                "system": system,
                "history": [dict(h) for h in history],
                "json_schema": json_schema,
                "use_mcp": use_mcp,
            }
        )
        return self.scripted.pop(0)


def _fake(monkeypatch, *texts: str) -> FakeProvider:
    fake = FakeProvider([LlmResponse(text=t) for t in texts])
    monkeypatch.setattr(llm_client, "get_provider", lambda: fake)
    return fake


def _generate(**kw: Any) -> list[dict[str, Any]]:
    return generate_checks_llm(
        table_name=kw.get("table_name", "people"),
        profile_summary=kw.get("profile_summary", PROFILE),
        knowledge=kw.get("knowledge"),
        exploration=kw.get("exploration"),
        existing_checks=kw.get("existing_checks", []),
        valid_columns=kw.get("valid_columns", COLUMNS),
    )


def _proposal(**over: Any) -> dict[str, Any]:
    base = {
        "name": "email present",
        "check_type": "not_null",
        "column_name": "email",
        "params": {},
        "severity": "error",
        "rationale": "5 of 200 rows have no email",
    }
    base.update(over)
    return base


# ------------------------------------------------------------------ parse path
def test_proposals_are_normalized_to_the_generator_shape(monkeypatch):
    _fake(
        monkeypatch,
        json.dumps({"checks": [_proposal(schedule_minutes=360)]}),
    )
    (proposal,) = _generate()

    assert proposal == {
        "name": "email present",
        "check_type": "not_null",
        "column_name": "email",
        "params": {},
        "severity": "error",
        "rationale": "5 of 200 rows have no email",
        "schedule_kind": "interval",
        "schedule_expr": "360",
    }


def test_markdown_fenced_json_is_parsed(monkeypatch):
    """Models routinely wrap JSON in a fence even when asked not to; the parser
    must tolerate it or the whole generation silently falls back to heuristics."""
    payload = json.dumps({"checks": [_proposal()]})
    for text in (f"```json\n{payload}\n```", f"```\n{payload}\n```"):
        _fake(monkeypatch, text)
        (proposal,) = _generate()
        assert proposal["check_type"] == "not_null"


def test_schedule_minutes_is_floored_and_defaulted(monkeypatch):
    _fake(
        monkeypatch,
        json.dumps(
            {
                "checks": [
                    _proposal(name="too fast", schedule_minutes=1),
                    _proposal(name="no schedule", column_name="age", check_type="not_null"),
                ]
            }
        ),
    )
    fast, default = _generate()
    assert fast["schedule_expr"] == "5"  # a 1-minute check would hammer the source
    assert default["schedule_expr"] == "1440"


def test_missing_optional_fields_fall_back_to_safe_defaults(monkeypatch):
    _fake(
        monkeypatch,
        json.dumps({"checks": [{"check_type": "not_null", "column_name": "email"}]}),
    )
    (proposal,) = _generate()
    assert proposal["severity"] == "warn"  # not "error" — an unlabelled check can't page
    assert proposal["name"] == ""
    assert proposal["rationale"] == ""


def test_table_level_check_without_a_column_is_kept(monkeypatch):
    _fake(
        monkeypatch,
        json.dumps(
            {"checks": [_proposal(check_type="row_count_min", column_name=None,
                                  params={"min_rows": 100})]}
        ),
    )
    (proposal,) = _generate()
    assert proposal["column_name"] is None
    assert proposal["params"]["min_rows"] == 100


# ------------------------------------------------------- per-proposal validation
def test_invalid_proposals_are_dropped_and_the_rest_survive(monkeypatch, caplog):
    _fake(
        monkeypatch,
        json.dumps(
            {
                "checks": [
                    _proposal(name="hallucinated type", check_type="vibes_check"),
                    _proposal(name="hallucinated column", column_name="emial"),
                    # accepted_values requires a `values` list
                    _proposal(name="missing param", check_type="accepted_values",
                              column_name="status", params={}),
                    _proposal(name="wrong param shape", check_type="accepted_values",
                              column_name="status", params={"values": "active"}),
                    # regex_match with an uncompilable pattern
                    _proposal(name="bad regex", check_type="regex_match", column_name="email",
                              params={"pattern": "([a-z"}),
                    _proposal(name="good one"),
                ]
            }
        ),
    )
    with caplog.at_level("WARNING"):
        proposals = _generate()

    assert [p["name"] for p in proposals] == ["good one"]
    # each drop is logged with the proposal name so an operator can see what happened
    assert "hallucinated type" in caplog.text
    assert "hallucinated column" in caplog.text


def test_column_names_are_checked_against_the_real_profile(monkeypatch):
    _fake(monkeypatch, json.dumps({"checks": [_proposal(column_name="ssn")]}))
    assert _generate(valid_columns={"id", "email"}) == []


def test_custom_sql_proposals_go_through_guard_sql(monkeypatch):
    """A custom_sql proposal becomes SQL this platform runs against a customer
    source on a schedule. guard_sql() is the gate for LLM-authored SQL too."""
    _fake(
        monkeypatch,
        json.dumps(
            {
                "checks": [
                    _proposal(name="write", check_type="custom_sql", column_name=None,
                              params={"sql": "DELETE FROM people"}),
                    _proposal(name="multi", check_type="custom_sql", column_name=None,
                              params={"sql": "SELECT 1; DROP TABLE people"}),
                    # #281/#267 class: a single SELECT with no write keyword that
                    # still reaches the host filesystem.
                    _proposal(name="host read", check_type="custom_sql", column_name=None,
                              params={"sql": "SELECT readfile('/etc/passwd') AS leak"}),
                    _proposal(name="legit", check_type="custom_sql", column_name=None,
                              params={"sql": "SELECT id FROM people WHERE age > 120"}),
                ]
            }
        ),
    )
    proposals = _generate()
    assert [p["name"] for p in proposals] == ["legit"]


# -------------------------------------------------------------- request shaping
def test_the_request_carries_the_registry_schema_and_mcp(monkeypatch):
    fake = _fake(monkeypatch, json.dumps({"checks": []}))
    _generate(exploration={"insights": [{"title": "nulls", "detail": "5 of 200"}]},
              existing_checks=["not_null on id [active]"])

    call = fake.seen[0]
    assert call["use_mcp"] is True
    schema = call["json_schema"]
    assert schema is CHECKS_SCHEMA
    enum = schema["properties"]["checks"]["items"]["properties"]["check_type"]["enum"]
    assert enum == sorted(CHECK_TYPES), "the schema drifted from the check registry"

    prompt = call["history"][0]["text"]
    assert "Exploration insights" in prompt
    assert "do NOT propose duplicates" in prompt
    assert "not_null on id [active]" in prompt


# ------------------------------------------------------------ graceful degrading
def test_unparseable_output_raises_for_the_caller_to_catch(monkeypatch):
    """generate_checks_llm deliberately does not swallow this — POST
    /checks/generate catches it and falls back to heuristics (asserted below)."""
    _fake(monkeypatch, "I'm sorry, I can't help with that.")
    with pytest.raises(json.JSONDecodeError):
        _generate()


def test_without_provider_raises_the_actionable_error(monkeypatch):
    monkeypatch.setattr(llm_client, "get_provider", lambda: None)
    with pytest.raises(RuntimeError, match="No LLM provider configured"):
        _generate()


# ------------------------------------------------------------------- end to end
def _dataset_with_profile(client, headers, source_db, unique_name) -> int:
    conn = client.post(
        "/api/v1/connections",
        json={"name": unique_name("checkgen-conn"), "dsn": source_db},
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
    assert client.post(f"/api/v1/datasets/{ds_id}/profile", headers=headers).status_code == 200
    return ds_id


def test_generate_uses_llm_proposals_and_marks_them_llm_origin(
    client, admin_headers, source_db, unique_name, monkeypatch
):
    ds_id = _dataset_with_profile(client, admin_headers, source_db, unique_name)
    _fake(
        monkeypatch,
        json.dumps({"checks": [_proposal(name="llm: email present", schedule_minutes=720)]}),
    )
    monkeypatch.setattr("app.api.checks.llm_enabled", lambda: True)

    resp = client.post(
        "/api/v1/checks/generate",
        json={"dataset_id": ds_id, "use_llm": True, "explore": False},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "llm"
    assert body["explored"] is False
    (check,) = body["checks"]
    assert check["name"] == "llm: email present"
    assert check["origin"] == "llm"
    assert check["status"] == "proposed"  # never auto-activated
    assert check["schedule_expr"] == "720"


def test_generate_falls_back_to_heuristics_on_garbage_output(
    client, admin_headers, source_db, unique_name, monkeypatch
):
    """A model that answers in prose must cost the analyst nothing: the endpoint
    logs, falls back to heuristics, and still returns 200 with real checks."""
    ds_id = _dataset_with_profile(client, admin_headers, source_db, unique_name)
    _fake(monkeypatch, "Sure! Here are some ideas: make sure emails are present.")
    monkeypatch.setattr("app.api.checks.llm_enabled", lambda: True)

    resp = client.post(
        "/api/v1/checks/generate",
        json={"dataset_id": ds_id, "use_llm": True, "explore": False},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "heuristic"
    assert resp.json()["created"] > 0
    assert all(c["origin"] == "heuristic" for c in resp.json()["checks"])


def test_generate_falls_back_when_every_proposal_is_invalid(
    client, admin_headers, source_db, unique_name, monkeypatch
):
    """Well-formed JSON whose every entry is bogus leaves zero proposals; the
    endpoint must still produce heuristics rather than an empty result."""
    ds_id = _dataset_with_profile(client, admin_headers, source_db, unique_name)
    _fake(monkeypatch, json.dumps({"checks": [_proposal(check_type="vibes_check")]}))
    monkeypatch.setattr("app.api.checks.llm_enabled", lambda: True)

    resp = client.post(
        "/api/v1/checks/generate",
        json={"dataset_id": ds_id, "use_llm": True, "explore": False},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "heuristic"
    assert resp.json()["created"] > 0


def test_generate_without_llm_key_still_generates_heuristics(
    client, admin_headers, source_db, unique_name
):
    ds_id = _dataset_with_profile(client, admin_headers, source_db, unique_name)
    resp = client.post(
        "/api/v1/checks/generate",
        json={"dataset_id": ds_id, "use_llm": True, "explore": False},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "heuristic"
    assert resp.json()["created"] > 0


def test_pii_columns_are_redacted_from_the_check_gen_prompt(
    client, admin_headers, source_db, unique_name, monkeypatch
):
    """Golden rule 8: a column marked PII in the dataset's knowledge must reach
    the model as a redaction marker, never as sample values."""
    ds_id = _dataset_with_profile(client, admin_headers, source_db, unique_name)
    saved = client.put(
        f"/api/v1/datasets/{ds_id}/knowledge",
        json={"business_context": "customer list", "pii_columns": ["email"]},
        headers=admin_headers,
    )
    assert saved.status_code == 200, saved.text

    fake = _fake(monkeypatch, json.dumps({"checks": [_proposal()]}))
    monkeypatch.setattr("app.api.checks.llm_enabled", lambda: True)
    resp = client.post(
        "/api/v1/checks/generate",
        json={"dataset_id": ds_id, "use_llm": True, "explore": False},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text

    prompt = fake.seen[0]["history"][0]["text"]
    assert "[values redacted: PII]" in prompt
    assert "@example.com" not in prompt
    # the knowledge block still tells the model WHICH columns were redacted
    assert "PII columns (values are redacted for you)" in prompt
    # non-PII columns keep their observed values, or the proposals get worse
    assert "'active'" in prompt
