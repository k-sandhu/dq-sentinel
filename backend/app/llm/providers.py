"""Provider-agnostic LLM layer (issue #47).

Two implementations behind one interface:
- AnthropicProvider: native Anthropic API (adaptive thinking, structured
  outputs, MCP connector for registered code-context servers).
- OpenAICompatProvider: ANY endpoint speaking the OpenAI chat-completions
  protocol via base_url + api_key (OpenRouter, vLLM, Ollama, Together, ...).

The loop logic upstream works on normalized history entries; each provider
keeps its own `raw` assistant payload in the history for full fidelity
(Anthropic thinking-block signatures, OpenAI tool_call ids).

Normalized history entries:
  {"role": "user", "text": str}
  {"role": "assistant", "response": LlmResponse}
  {"role": "tool_results", "results": [{"id", "content", "is_error"}]}
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.config import get_settings
from app.observability import LLM_LATENCY, LLM_REQUESTS, LLM_TOKENS

log = logging.getLogger(__name__)

_ADAPTIVE_RE = re.compile(r"(fable|opus-4-[6-9]|sonnet-4-[6-9])")


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LlmResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end"  # end | tool_use | pause
    usage_in: int = 0
    usage_out: int = 0
    raw: Any = None  # provider-native assistant payload (history fidelity)


class BaseProvider:
    name = "base"

    def __init__(self, model: str):
        self.model = model

    def complete(
        self,
        system: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        use_mcp: bool = False,
    ) -> LlmResponse:
        start = time.perf_counter()
        outcome = "error"
        try:
            response = self._complete(system, history, tools, json_schema, max_tokens, use_mcp)
            outcome = "ok"
            LLM_TOKENS.labels(self.name, self.model, "input").inc(response.usage_in)
            LLM_TOKENS.labels(self.name, self.model, "output").inc(response.usage_out)
            return response
        finally:
            LLM_REQUESTS.labels(self.name, self.model, outcome).inc()
            LLM_LATENCY.labels(self.name, self.model).observe(time.perf_counter() - start)

    def _complete(self, system, history, tools, json_schema, max_tokens, use_mcp) -> LlmResponse:
        raise NotImplementedError


# --------------------------------------------------------------- anthropic
class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str):
        super().__init__(model)
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)

    @staticmethod
    def _serialize(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for entry in history:
            if entry["role"] == "user":
                messages.append({"role": "user", "content": entry["text"]})
            elif entry["role"] == "assistant":
                resp: LlmResponse = entry["response"]
                content = resp.raw if resp.raw is not None else (resp.text or "...")
                messages.append({"role": "assistant", "content": content})
            elif entry["role"] == "tool_results":
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r["id"],
                                "content": r["content"],
                                "is_error": bool(r.get("is_error")),
                            }
                            for r in entry["results"]
                        ],
                    }
                )
        return messages

    def _complete(self, system, history, tools, json_schema, max_tokens, use_mcp) -> LlmResponse:
        settings = get_settings()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or settings.llm_max_output_tokens,
            "system": system,
            "messages": self._serialize(history),
        }
        if _ADAPTIVE_RE.search(self.model):
            kwargs["thinking"] = {"type": "adaptive"}
        if tools:
            kwargs["tools"] = tools
        if json_schema:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": json_schema}}

        response = None
        if use_mcp:
            from app.llm.client import enabled_mcp_servers

            servers = enabled_mcp_servers()
            if servers:
                try:
                    response = self._client.beta.messages.create(
                        **kwargs, mcp_servers=servers, betas=["mcp-client-2025-11-20"]
                    )
                except Exception as exc:  # noqa: BLE001 - MCP connector is best-effort
                    log.warning("MCP connector call failed (%s); retrying without", exc)
        if response is None:
            response = self._client.messages.create(**kwargs)

        text = "".join(b.text for b in response.content if b.type == "text")
        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=dict(b.input))
            for b in response.content
            if b.type == "tool_use"
        ]
        stop = {"tool_use": "tool_use", "pause_turn": "pause"}.get(response.stop_reason, "end")
        usage = getattr(response, "usage", None)
        return LlmResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop,
            usage_in=getattr(usage, "input_tokens", 0) or 0,
            usage_out=getattr(usage, "output_tokens", 0) or 0,
            raw=response.content,
        )


# --------------------------------------------------- openai-compatible
class OpenAICompatProvider(BaseProvider):
    """Works with any OpenAI-compatible endpoint (OpenRouter et al.)."""

    name = "openai"

    def __init__(self, model: str, api_key: str, base_url: str):
        super().__init__(model)
        import openai

        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

    @staticmethod
    def _serialize(system: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for entry in history:
            if entry["role"] == "user":
                messages.append({"role": "user", "content": entry["text"]})
            elif entry["role"] == "assistant":
                resp: LlmResponse = entry["response"]
                if resp.raw is not None:
                    messages.append(resp.raw)
                else:
                    messages.append({"role": "assistant", "content": resp.text or "..."})
            elif entry["role"] == "tool_results":
                for r in entry["results"]:
                    content = r["content"]
                    if r.get("is_error"):
                        content = f"ERROR: {content}"
                    messages.append(
                        {"role": "tool", "tool_call_id": r["id"], "content": content}
                    )
        return messages

    def _request(self, messages, tools, json_schema, max_tokens):
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        if json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "strict": False, "schema": json_schema},
            }
        return self._client.chat.completions.create(**kwargs)

    def _complete(self, system, history, tools, json_schema, max_tokens, use_mcp) -> LlmResponse:
        # MCP connector is Anthropic-specific; OpenAI-compatible path ignores it.
        del use_mcp
        settings = get_settings()
        messages = self._serialize(system, history)
        converted = self._convert_tools(tools)
        max_tokens = max_tokens or settings.llm_max_output_tokens

        try:
            response = self._request(messages, converted, json_schema, max_tokens)
        except Exception as exc:  # noqa: BLE001
            if json_schema is None:
                raise
            # Some models/providers reject response_format — fall back to
            # embedding the schema in the prompt; downstream parsing is tolerant.
            log.warning("response_format rejected by %s (%s); falling back to prompt schema", self.model, exc)
            fallback = list(messages)
            fallback.append(
                {
                    "role": "user",
                    "content": "Respond with ONLY a JSON object (no prose, no markdown fences) "
                    f"matching this JSON schema:\n{json.dumps(json_schema)}",
                }
            )
            response = self._request(fallback, converted, None, max_tokens)

        choice = response.choices[0]
        message = choice.message
        tool_calls = []
        for tc in message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))

        raw = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            raw["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ]

        usage = getattr(response, "usage", None)
        return LlmResponse(
            text=message.content or "",
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end",
            usage_in=getattr(usage, "prompt_tokens", 0) or 0,
            usage_out=getattr(usage, "completion_tokens", 0) or 0,
            raw=raw,
        )


@lru_cache(maxsize=4)
def _build_provider(provider: str, model: str, base_url: str | None, api_key: str) -> BaseProvider:
    if provider == "anthropic":
        return AnthropicProvider(model, api_key)
    return OpenAICompatProvider(model, api_key, base_url or "https://openrouter.ai/api/v1")


def get_provider() -> BaseProvider | None:
    resolved = get_settings().resolved_llm()
    if resolved is None:
        return None
    return _build_provider(
        resolved["provider"], resolved["model"], resolved["base_url"], resolved["api_key"]
    )
