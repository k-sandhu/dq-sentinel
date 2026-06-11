"""Provider abstraction: resolution from config, serializers, and the agent
loop running provider-agnostically (FakeProvider, no network)."""


from app.config import Settings
from app.llm import client as llm_client
from app.llm.providers import BaseProvider, LlmResponse, OpenAICompatProvider, ToolCall


# ---------------------------------------------------------------- resolution
def _settings(**kw) -> Settings:
    base = {
        "anthropic_api_key": "",
        "llm_api_key": "",
        "llm_model": "",
        "llm_provider": "auto",
        "secret_key": "x" * 32,
    }
    base.update(kw)
    return Settings(**base, _env_file=None)


def test_resolution_auto_prefers_anthropic():
    s = _settings(anthropic_api_key="sk-ant-xxx")
    resolved = s.resolved_llm()
    assert resolved["provider"] == "anthropic"
    assert resolved["model"] == "claude-opus-4-8"  # per-provider default
    assert s.llm_enabled


def test_resolution_auto_falls_back_to_openai_compatible():
    s = _settings(llm_api_key="sk-or-xxx", llm_model="anthropic/claude-sonnet-4.6")
    resolved = s.resolved_llm()
    assert resolved["provider"] == "openai"
    assert resolved["base_url"] == "https://openrouter.ai/api/v1"  # OpenRouter default
    assert s.llm_enabled


def test_resolution_openai_requires_model():
    s = _settings(llm_api_key="sk-or-xxx")  # no model -> disabled, not half-configured
    assert s.resolved_llm() is None
    assert not s.llm_enabled


def test_resolution_explicit_provider_and_custom_base_url():
    s = _settings(
        llm_provider="openrouter",
        llm_api_key="k",
        llm_model="meta-llama/llama-3.3-70b",
        llm_base_url="http://vllm.internal:8000/v1",
        anthropic_api_key="sk-ant-should-be-ignored",
    )
    resolved = s.resolved_llm()
    assert resolved["provider"] == "openai"
    assert resolved["base_url"] == "http://vllm.internal:8000/v1"


def test_resolution_disabled_without_keys():
    assert _settings().resolved_llm() is None


# ---------------------------------------------------------------- serializers
def test_openai_serializer_round_trip():
    response = LlmResponse(
        text="checking",
        tool_calls=[ToolCall(id="c1", name="run_sql", input={"sql": "SELECT 1"})],
        stop_reason="tool_use",
        raw={
            "role": "assistant",
            "content": "checking",
            "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "run_sql", "arguments": '{"sql": "SELECT 1"}'}}
            ],
        },
    )
    history = [
        {"role": "user", "text": "investigate"},
        {"role": "assistant", "response": response},
        {"role": "tool_results", "results": [{"id": "c1", "content": "1 row", "is_error": False}]},
    ]
    messages = OpenAICompatProvider._serialize("sys prompt", history)
    assert messages[0] == {"role": "system", "content": "sys prompt"}
    assert messages[1] == {"role": "user", "content": "investigate"}
    assert messages[2]["tool_calls"][0]["id"] == "c1"  # raw payload preserved verbatim
    assert messages[3] == {"role": "tool", "tool_call_id": "c1", "content": "1 row"}


def test_openai_tool_conversion():
    converted = OpenAICompatProvider._convert_tools([llm_client.RUN_SQL_TOOL])
    assert converted[0]["type"] == "function"
    assert converted[0]["function"]["name"] == "run_sql"
    assert "sql" in converted[0]["function"]["parameters"]["properties"]


def test_openai_null_choices_raises_clear_error(monkeypatch):
    """OpenRouter can return 200 with choices=null + an error body; that must
    surface as a readable RuntimeError, not 'NoneType is not subscriptable'."""

    class FakeNullResponse:
        choices = None
        error = {"message": "Provider returned error", "code": 429}

    provider = OpenAICompatProvider.__new__(OpenAICompatProvider)  # skip SDK init
    provider.model = "vendor/some-model"
    monkeypatch.setattr(
        OpenAICompatProvider, "_request", lambda self, *a, **k: FakeNullResponse()
    )

    import pytest

    with pytest.raises(RuntimeError, match="no choices.*Provider returned error"):
        provider._complete("sys", [{"role": "user", "text": "hi"}], None, None, 100, False)


def test_anthropic_serializer_uses_raw_blocks():
    from app.llm.providers import AnthropicProvider

    raw_blocks = [{"type": "text", "text": "hi"}]  # stands in for SDK content blocks
    history = [
        {"role": "user", "text": "go"},
        {"role": "assistant", "response": LlmResponse(text="hi", raw=raw_blocks)},
        {"role": "tool_results", "results": [{"id": "t1", "content": "ok", "is_error": True}]},
    ]
    messages = AnthropicProvider._serialize(history)
    assert messages[1]["content"] is raw_blocks  # verbatim, preserves thinking signatures
    assert messages[2]["content"][0]["tool_use_id"] == "t1"
    assert messages[2]["content"][0]["is_error"] is True


# ---------------------------------------------------------------- agent loop
class FakeProvider(BaseProvider):
    name = "fake"

    def __init__(self, scripted: list[LlmResponse]):
        super().__init__("fake-model")
        self.scripted = list(scripted)
        self.seen_histories: list[list] = []

    def _complete(self, system, history, tools, json_schema, max_tokens, use_mcp) -> LlmResponse:
        self.seen_histories.append([dict(h) for h in history])
        return self.scripted.pop(0)


FINAL_TOOL = {
    "name": "submit",
    "description": "finish",
    "input_schema": {"type": "object", "properties": {"answer": {"type": "string"}},
                     "required": ["answer"], "additionalProperties": False},
}


def test_agent_loop_runs_tools_then_finishes(monkeypatch):
    fake = FakeProvider(
        [
            LlmResponse(
                text="let me check",
                tool_calls=[ToolCall("a1", "run_sql", {"sql": "SELECT 1", "purpose": "probe"})],
                stop_reason="tool_use",
            ),
            LlmResponse(
                text="",
                tool_calls=[ToolCall("a2", "submit", {"answer": "all good"})],
                stop_reason="tool_use",
            ),
        ]
    )
    monkeypatch.setattr(llm_client, "get_provider", lambda: fake)

    executed: list[str] = []

    def run_sql(inp):
        executed.append(inp["sql"])
        return "col\n1\n(1 rows)"

    transcript: list[dict] = []
    result = llm_client.run_agent_loop(
        system="sys",
        user_prompt="check the data",
        handlers={"run_sql": run_sql},
        tools=[llm_client.RUN_SQL_TOOL],
        final_tool=FINAL_TOOL,
        max_turns=5,
        transcript=transcript,
    )

    assert result == {"answer": "all good"}
    assert executed == ["SELECT 1"]
    types = [t["type"] for t in transcript]
    assert types == ["text", "sql", "result", "final"]
    # second call's history carries the tool result back to the model
    second = fake.seen_histories[1]
    assert second[-1]["role"] == "tool_results"
    assert second[-1]["results"][0]["content"].startswith("col")


def test_agent_loop_feeds_errors_back_and_expires(monkeypatch):
    fake = FakeProvider(
        [
            LlmResponse(text="", tool_calls=[ToolCall("x", "run_sql", {"sql": "BAD", "purpose": ""})],
                        stop_reason="tool_use"),
            LlmResponse(text="hmm", stop_reason="end"),  # nudge 1
            LlmResponse(text="still thinking", stop_reason="end"),  # nudge 2
            LlmResponse(text="...", stop_reason="end"),  # nudge 3 -> loop gives up
        ]
    )
    monkeypatch.setattr(llm_client, "get_provider", lambda: fake)

    def run_sql(_inp):
        raise ValueError("syntax error")

    transcript: list[dict] = []
    result = llm_client.run_agent_loop(
        system="s", user_prompt="p",
        handlers={"run_sql": run_sql},
        tools=[llm_client.RUN_SQL_TOOL],
        final_tool=FINAL_TOOL,
        max_turns=10,
        transcript=transcript,
    )
    assert result is None
    error_step = next(t for t in transcript if t["type"] == "result")
    assert error_step["error"] is True
    assert "syntax error" in error_step["content"]
