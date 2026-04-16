"""Model backend abstraction for the operator LLM.

Defines a protocol for chat completion backends and provides an
OpenAI-compatible HTTP implementation suitable for local inference
servers (llama.cpp, vLLM, Ollama, etc.) and remote APIs.

The backend is intentionally thin — it sends messages and tool
definitions, receives assistant messages with optional tool calls.
All orchestration logic lives in the REPL / conversation loop.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AssistantMessage:
    """Response from the model backend."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""


class ChatBackend(Protocol):
    """Protocol for a chat completion backend."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
    ) -> AssistantMessage: ...

    @property
    def model_name(self) -> str: ...

    def available(self) -> bool: ...


@dataclass
class OpenAICompatibleBackend:
    """OpenAI-compatible HTTP chat completion backend.

    Works with any server that implements the /v1/chat/completions
    endpoint: OpenAI, OpenRouter, Ollama, llama.cpp server, vLLM, etc.

    For local servers (Ollama, llama.cpp), set base_url to the local
    endpoint and api_key to an empty string or dummy value.
    """

    base_url: str
    api_key: str = ""
    model: str = ""
    timeout_seconds: int = 180  # small local models with tools need headroom

    @property
    def model_name(self) -> str:
        return self.model or "unknown"

    def available(self) -> bool:
        """Check if the backend is reachable."""
        try:
            url = f"{self.base_url.rstrip('/')}/v1/models"
            req = urllib.request.Request(url, method="GET")
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")
            with urllib.request.urlopen(req, timeout=5):
                return True
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
    ) -> AssistantMessage:
        """Send a chat completion request and parse the response."""
        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
            },
        )
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8")[:500]
            except Exception:
                pass
            raise RuntimeError(
                f"Backend returned HTTP {exc.code}: {error_body}"
            ) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise RuntimeError(f"Backend unreachable: {exc}") from exc

        return self._parse_response(data)

    def _parse_response(self, data: dict[str, Any]) -> AssistantMessage:
        choices = data.get("choices", [])
        if not choices:
            return AssistantMessage(content="", raw=data)

        choice = choices[0]
        message = choice.get("message", {})
        content = message.get("content") or ""
        finish_reason = choice.get("finish_reason", "")

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls", []):
            func = tc.get("function", {})
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {"_raw": func.get("arguments", "")}
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=func.get("name", ""),
                    arguments=args,
                )
            )

        # Reasoning models (qwen3, etc.) put chain-of-thought in a
        # ``reasoning`` field and leave ``content`` empty. When there
        # are no tool calls (i.e. this is the final answer), fall back
        # to ``reasoning`` so the operator sees the model's output.
        # During tool-call turns we ignore reasoning — it's internal
        # process, not the user-facing answer.
        if not content and not tool_calls:
            content = message.get("reasoning") or ""

        return AssistantMessage(
            content=content,
            tool_calls=tool_calls,
            raw=data,
            finish_reason=finish_reason,
        )


def create_backend(
    *,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    timeout_seconds: int = 180,
) -> OpenAICompatibleBackend:
    """Create a backend from configuration.

    Sensible defaults for common local inference servers:
    - Ollama: base_url=http://localhost:11434
    - llama.cpp: base_url=http://localhost:8080
    - vLLM: base_url=http://localhost:8000
    """
    if not base_url:
        base_url = "http://localhost:11434"  # Ollama default
    return OpenAICompatibleBackend(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
    )
