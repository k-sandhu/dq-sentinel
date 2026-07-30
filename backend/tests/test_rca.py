"""RCA agent: the structured report contract (#287).

Drives the real background task on a scripted FakeProvider (no network) and
asserts the structured report survives all the way to the API — the bug was that
`report_json` never existed, so the frontend's structured renderer was dead code.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.llm import client as llm_client
from app.llm.providers import BaseProvider, LlmResponse, ToolCall
from app.llm.rca_agent import build_report_json
from app.schemas import RcaOut


class FakeProvider(BaseProvider):
    name = "fake"

    def __init__(self, scripted: list[LlmResponse]):
        super().__init__("fake-model")
        self.scripted = list(scripted)
        self.seen: list[tuple[str, list]] = []

    def _complete(self, system, history, tools, json_schema, max_tokens, use_mcp) -> LlmResponse:
        self.seen.append((system, [dict(h) for h in history]))
        return self.scripted.pop(0)


def _dataset(client, headers, source_db) -> int:
    conn = client.post(
        "/api/v1/connections",
        json={"name": f"rca-{uuid4().hex}", "dsn": source_db},
        headers=headers,
    )
    assert conn.status_code == 201, conn.text
    resp = client.post(
        "/api/v1/datasets/register",
        json={"connection_id": conn.json()["id"], "tables": [{"table_name": "people"}]},
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()[0]["id"]


def _enable_llm(monkeypatch, provider: FakeProvider) -> None:
    monkeypatch.setattr("app.api.rca.llm_enabled", lambda: True)
    monkeypatch.setattr(llm_client, "get_provider", lambda: provider)


def _start(client, headers, dataset_id: int, question: str) -> dict:
    # TestClient drains BackgroundTasks before returning, so the agent has already run.
    resp = client.post(
        "/api/v1/rca/start",
        json={"dataset_id": dataset_id, "question": question},
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


STRUCTURED_REPORT = {
    "root_cause_summary": "A backfill job wrote 5 rows with no email.",
    "report_md": "## What happened\nA backfill wrote NULL emails.",
    "confidence": "high",
    "suggested_fixes": ["Re-run the backfill with the email column mapped"],
    "likely_cause": "The 2026-06 backfill dropped the email mapping.",
    "hypotheses": [
        {
            "statement": "The nulls are concentrated in the oldest rows",
            "verdict": "supported",
            "evidence": "All 5 nulls have id <= 5",
        },
        {
            "statement": "A source-system change removed the column",
            # Not one of the contract's verdicts -> normalized, never upgraded to supported.
            "verdict": "probably not",
            "evidence": "The column is still present upstream",
        },
        {"statement": "   ", "verdict": "supported", "evidence": "no statement -> dropped"},
    ],
    "evidence": [
        {
            "title": "Null emails by id range",
            "sql": "SELECT COUNT(*) AS n FROM people WHERE email IS NULL",
            "finding": "5 rows, all id <= 5",
        },
        {"sql": "SELECT 1", "finding": "no title -> dropped"},
    ],
    "recommended_actions": [
        {"action": "Re-run the backfill", "kind": "fix_pipeline"},
        {"action": "Confirm with the source team", "kind": "ask_someone"},
    ],
}


def _agent_script(final_input: dict) -> FakeProvider:
    return FakeProvider(
        [
            LlmResponse(
                text="Let me count the null emails.",
                tool_calls=[
                    ToolCall(
                        "t1",
                        "run_sql",
                        {
                            "sql": "SELECT COUNT(*) AS n FROM people WHERE email IS NULL",
                            "purpose": "count null emails",
                        },
                    )
                ],
                stop_reason="tool_use",
            ),
            LlmResponse(
                text="",
                tool_calls=[ToolCall("t2", "get_table_code", {"table": "people"})],
                stop_reason="tool_use",
            ),
            LlmResponse(
                text="",
                tool_calls=[ToolCall("t3", "submit_report", final_input)],
                stop_reason="tool_use",
            ),
        ]
    )


# --------------------------------------------------------------- structured run
def test_structured_report_is_persisted_and_served(client, admin_headers, source_db, monkeypatch):
    ds_id = _dataset(client, admin_headers, source_db)
    _enable_llm(monkeypatch, _agent_script(STRUCTURED_REPORT))

    started = _start(client, admin_headers, ds_id, "why are some emails null?")

    detail = client.get(f"/api/v1/rca/{started['id']}", headers=admin_headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "complete", body["report_md"]

    report = body["report_json"]
    assert report is not None, "structured report was discarded (#287)"
    assert report["version"] == 1
    assert report["confidence"] == "high"
    assert report["likely_cause"] == "The 2026-06 backfill dropped the email mapping."

    # entries missing their required text are dropped; unknown enums fall back safely
    assert [(h["statement"], h["verdict"]) for h in report["hypotheses"]] == [
        ("The nulls are concentrated in the oldest rows", "supported"),
        ("A source-system change removed the column", "inconclusive"),
    ]
    assert [e["title"] for e in report["evidence"]] == ["Null emails by id range"]
    assert [(a["action"], a["kind"]) for a in report["recommended_actions"]] == [
        ("Re-run the backfill", "fix_pipeline"),
        ("Confirm with the source team", "investigate"),
    ]

    # the markdown report is still written — it stays the human fallback
    assert "What happened" in body["report_md"]
    assert "Re-run the backfill with the email column mapped" in body["report_md"]
    assert body["root_cause_summary"].startswith("A backfill job")


def test_report_json_exposed_by_list_endpoint(client, admin_headers, source_db, monkeypatch):
    ds_id = _dataset(client, admin_headers, source_db)
    _enable_llm(monkeypatch, _agent_script(STRUCTURED_REPORT))
    started = _start(client, admin_headers, ds_id, "list endpoint must carry the report")

    listed = client.get(f"/api/v1/rca?dataset_id={ds_id}", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    rows = [r for r in listed.json() if r["id"] == started["id"]]
    assert rows, listed.json()
    assert rows[0]["report_json"] is not None
    assert rows[0]["report_json"]["hypotheses"][0]["verdict"] == "supported"


def test_transcript_records_non_sql_tool_steps(client, admin_headers, source_db, monkeypatch):
    """The loop emits `tool` steps for non-SQL tools; they must reach the client or
    the following `result` block renders orphaned in the transcript."""
    ds_id = _dataset(client, admin_headers, source_db)
    _enable_llm(monkeypatch, _agent_script(STRUCTURED_REPORT))
    started = _start(client, admin_headers, ds_id, "transcript shape")

    steps = client.get(f"/api/v1/rca/{started['id']}", headers=admin_headers).json()["transcript"]
    assert [s["type"] for s in steps][:3] == ["text", "sql", "result"]
    tool_steps = [s for s in steps if s["type"] == "tool"]
    assert tool_steps and tool_steps[0]["name"] == "get_table_code"
    assert tool_steps[0]["content"] == {"table": "people"}
    # every tool/sql step is followed by its result — none are orphaned
    for i, step in enumerate(steps):
        if step["type"] in ("sql", "tool"):
            assert steps[i + 1]["type"] == "result", steps


# ------------------------------------------------------------ graceful degradation
def test_legacy_markdown_only_report_still_succeeds(client, admin_headers, source_db, monkeypatch):
    """A weaker model that fills only the legacy fields must still produce a complete
    session — report_json is simply NULL and the UI falls back to markdown."""
    ds_id = _dataset(client, admin_headers, source_db)
    _enable_llm(
        monkeypatch,
        _agent_script(
            {
                "root_cause_summary": "Nulls come from an old import.",
                "report_md": "## Findings\nNulls come from an old import.",
                "confidence": "medium",
                "suggested_fixes": ["Backfill the missing emails"],
            }
        ),
    )
    started = _start(client, admin_headers, ds_id, "legacy shape")

    body = client.get(f"/api/v1/rca/{started['id']}", headers=admin_headers).json()
    assert body["status"] == "complete"
    assert body["report_json"] is None
    assert "old import" in body["report_md"]
    assert "Backfill the missing emails" in body["report_md"]


def test_unreadable_stored_report_degrades_to_markdown(client, admin_headers, source_db):
    """report_json is a free-form JSON column: a payload we cannot parse must not
    500 the RCA tab."""
    from app.db import session_factory
    from app.models import RcaSession

    ds_id = _dataset(client, admin_headers, source_db)
    with session_factory()() as db:
        session = RcaSession(
            dataset_id=ds_id,
            question="corrupt payload",
            status="complete",
            report_md="markdown still readable",
            report_json={"version": 9, "hypotheses": "not a list"},
        )
        db.add(session)
        db.commit()
        session_id = session.id

    body = client.get(f"/api/v1/rca/{session_id}", headers=admin_headers)
    assert body.status_code == 200, body.text
    assert body.json()["report_json"] is None
    assert body.json()["report_md"] == "markdown still readable"


def test_foreign_stored_report_degrades_to_markdown(client, admin_headers, source_db):
    """A payload that is valid JSON of *another* shape must not be served as a
    structured report (#312). Every RcaReport field is optional, so it used to
    validate into an empty report and the UI implied the agent found nothing —
    while the narrative it did have sat unused in report_md."""
    from app.db import session_factory
    from app.models import RcaSession

    ds_id = _dataset(client, admin_headers, source_db)
    with session_factory()() as db:
        session = RcaSession(
            dataset_id=ds_id,
            question="foreign payload",
            status="complete",
            report_md="the narrative is all we have",
            report_json={"totally": "wrong shape"},
        )
        db.add(session)
        db.commit()
        session_id = session.id

    body = client.get(f"/api/v1/rca/{session_id}", headers=admin_headers)
    assert body.status_code == 200, body.text
    assert body.json()["report_json"] is None
    assert body.json()["report_md"] == "the narrative is all we have"


def _rca_out(report_json) -> RcaOut:
    """RcaOut around a stored report_json value; everything else is filler."""
    return RcaOut.model_validate(
        {
            "id": 1,
            "dataset_id": 2,
            "check_run_id": None,
            "question": "why",
            "status": "complete",
            "report_md": "# narrative",
            "report_json": report_json,
            "root_cause_summary": "a backfill",
            "transcript": [],
            "model": "fake-model",
            "created_at": datetime(2026, 7, 1, tzinfo=UTC),
            "finished_at": None,
        }
    )


def test_rca_out_serves_a_genuine_agent_report():
    """The gate added for #312 must not reject the shape rca_agent actually writes."""
    stored = build_report_json(STRUCTURED_REPORT)
    assert stored is not None and stored["version"] == 1  # what the column holds

    report = _rca_out(stored).report_json
    assert report is not None
    assert report.version == 1
    assert report.confidence == "high"
    assert report.likely_cause == "The 2026-06 backfill dropped the email mapping."
    assert [h.verdict for h in report.hypotheses] == ["supported", "inconclusive"]
    assert [e.title for e in report.evidence] == ["Null emails by id range"]
    assert [a.kind for a in report.recommended_actions] == ["fix_pipeline", "investigate"]


@pytest.mark.parametrize(
    "stored",
    [
        {"version": 1},  # ours, but empty of content
        {"likely_cause": "a stale upstream view"},  # a thinner/older shape of ours
        {"hypotheses": [{"statement": "s", "verdict": "supported"}]},
        {"evidence": [{"title": "t"}]},
        {"recommended_actions": [{"action": "a", "kind": "fix_data"}]},
    ],
)
def test_rca_out_keeps_payloads_carrying_our_keys(stored):
    assert _rca_out(stored).report_json is not None, stored


@pytest.mark.parametrize(
    "stored",
    [
        {"totally": "wrong shape"},  # #312: validated into an empty report
        {},
        {"confidence": "high"},  # a badge with nothing behind it is not a report
        {"version": 9, "hypotheses": "not a list"},  # ours-ish, but unreadable
        {"hypotheses": [{"statement": "s", "verdict": "maybe"}]},  # verdict off-contract
        "a bare string",
        [{"statement": "s"}],
        7,
    ],
)
def test_rca_out_degrades_unreadable_payloads_to_none(stored):
    out = _rca_out(stored)
    assert out.report_json is None, stored
    assert out.report_md == "# narrative"  # the narrative is untouched


def test_rca_start_still_503_without_llm(client, admin_headers, source_db):
    ds_id = _dataset(client, admin_headers, source_db)
    resp = client.post(
        "/api/v1/rca/start", json={"dataset_id": ds_id, "question": "anything"}, headers=admin_headers
    )
    assert resp.status_code == 503
    assert "LLM" in resp.json()["detail"]


# ------------------------------------------------------------------ normalizer
def test_build_report_json_tolerates_fenced_json_strings():
    """Models routinely hand back an array field as a markdown-fenced JSON string."""
    report = build_report_json(
        {
            "confidence": "LOW",
            "likely_cause": "  a stale upstream view  ",
            "hypotheses": '```json\n[{"statement": "stale view", "verdict": "supported"}]\n```',
            "recommended_actions": '[{"action": "refresh the view", "kind": "fix_pipeline"}]',
        }
    )
    assert report == {
        "version": 1,
        "hypotheses": [{"statement": "stale view", "verdict": "supported", "evidence": ""}],
        "evidence": [],
        "likely_cause": "a stale upstream view",
        "recommended_actions": [{"action": "refresh the view", "kind": "fix_pipeline"}],
        "confidence": "low",
    }


def test_build_report_json_returns_none_without_structure():
    assert build_report_json({"report_md": "# text", "confidence": "high", "suggested_fixes": ["x"]}) is None
    assert build_report_json({"hypotheses": "not json at all", "evidence": []}) is None
