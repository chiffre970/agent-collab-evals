"""Deterministic, in-process model stream for no-spend integration rehearsals."""

from __future__ import annotations

import json
import threading
from typing import Any

from ..model_gateway import UpstreamStream


class DeterministicModelUpstream:
    """Return one valid OpenAI-compatible stream without network access."""

    def __init__(
        self,
        *,
        model: str,
        provider: str,
        content: str = "LOCAL_ADAPTER_REHEARSAL_OK",
    ) -> None:
        if not model or not provider or not content:
            raise ValueError("deterministic model identity and content are required")
        self._model = model
        self._provider = provider
        self._content = content
        self._requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def requests(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(dict(request) for request in self._requests)

    def stream(self, request: bytes) -> UpstreamStream:
        value = json.loads(request)
        if not isinstance(value, dict):
            raise ValueError("deterministic model request must be an object")
        with self._lock:
            self._requests.append(dict(value))
            ordinal = len(self._requests)
        request_id = f"local-adapter-rehearsal-{ordinal:04d}"
        chunks = (
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": 1_788_000_000,
                "model": self._model,
                "system_fingerprint": "deterministic-local-v1",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": self._content},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": 1_788_000_000,
                "model": self._model,
                "system_fingerprint": "deterministic-local-v1",
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            },
        )
        body = "".join(
            f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"
            for chunk in chunks
        ) + "data: [DONE]\n\n"
        return UpstreamStream(
            status=200,
            headers={"Content-Type": "text/event-stream"},
            chunks=(body.encode("utf-8"),),
            provider_name=self._provider,
        )


class DeterministicToolModelUpstream:
    """Exercise the native or peer tool surface without external inference."""

    def __init__(self, *, model: str, provider: str, peer_actor_count: int) -> None:
        if not model or not provider or peer_actor_count < 1:
            raise ValueError("deterministic tool-model configuration is invalid")
        self._model = model
        self._provider = provider
        self._peer_actor_count = peer_actor_count
        self._requests: list[dict[str, Any]] = []
        self._tool_calls: list[str] = []
        self._raw_requests: list[bytes] = []
        self._condition = threading.Condition()

    @property
    def raw_requests(self) -> tuple[bytes, ...]:
        with self._condition:
            return tuple(self._raw_requests)

    @property
    def requests(self) -> tuple[dict[str, Any], ...]:
        with self._condition:
            return tuple(dict(request) for request in self._requests)

    @property
    def tool_calls(self) -> tuple[str, ...]:
        with self._condition:
            return tuple(self._tool_calls)

    def stream(self, request: bytes) -> UpstreamStream:
        value = json.loads(request)
        if not isinstance(value, dict):
            raise ValueError("deterministic model request must be an object")
        with self._condition:
            self._requests.append(dict(value))
            self._raw_requests.append(request)
            ordinal = len(self._requests)
        tools = {
            str(tool.get("function", {}).get("name")): tool
            for tool in value.get("tools", [])
            if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
        }
        messages = value.get("messages", [])
        if not isinstance(messages, list):
            raise ValueError("deterministic model messages must be a list")
        last_user = max(
            (
                index
                for index, message in enumerate(messages)
                if isinstance(message, dict) and message.get("role") == "user"
            ),
            default=-1,
        )
        tool_results = sum(
            isinstance(message, dict) and message.get("role") == "tool"
            for message in messages[last_user + 1 :]
        )
        request_id = f"local-tool-rehearsal-{ordinal:04d}"
        if "peer_publish" in tools:
            if tool_results == 0:
                previous_tools = _message_tool_names(messages[: last_user + 1])
                if "peer_publish" in previous_tools:
                    return self._tool_stream(
                        request_id,
                        "peer_list_recent",
                        {"cursor": None, "limit": 50},
                    )
                return self._tool_stream(
                    request_id,
                    "peer_publish",
                    {
                        "idempotency_key": f"synthetic-peer-{ordinal:04d}",
                        "body": f"synthetic peer finding {ordinal:04d}",
                        "reply_to": None,
                    },
                )
            return self._text_stream(request_id, "PEER_TOOLS_OK")
        if "task" in tools and tool_results == 0:
            return self._tool_stream(
                request_id,
                "task",
                _task_arguments(tools["task"]),
            )
        return self._text_stream(
            request_id,
            "CHILD_OK" if _user_text(messages).find("CHILD_OK") >= 0 else "RUNTIME_OK",
        )

    def _text_stream(self, request_id: str, content: str) -> UpstreamStream:
        chunks = (
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": 1_788_000_000,
                "model": self._model,
                "system_fingerprint": "deterministic-tool-local-v1",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": content},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": 1_788_000_000,
                "model": self._model,
                "system_fingerprint": "deterministic-tool-local-v1",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": _usage(2),
            },
        )
        return _stream(chunks, self._provider)

    def _tool_stream(
        self, request_id: str, name: str, arguments: dict[str, Any]
    ) -> UpstreamStream:
        with self._condition:
            self._tool_calls.append(name)
        chunks = (
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": 1_788_000_000,
                "model": self._model,
                "system_fingerprint": "deterministic-tool-local-v1",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": f"call-{request_id}",
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(
                                            arguments, separators=(",", ":")
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": 1_788_000_000,
                "model": self._model,
                "system_fingerprint": "deterministic-tool-local-v1",
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
                ],
                "usage": _usage(4),
            },
        )
        return _stream(chunks, self._provider)


def _task_arguments(tool: dict[str, Any]) -> dict[str, Any]:
    function = tool.get("function", {})
    schema = function.get("parameters", {})
    properties = schema.get("properties", {})
    required = schema.get("required", list(properties))
    known = {
        "agent": "general",
        "subagent_type": "general",
        "description": "Synthetic rehearsal child",
        "prompt": "Return exactly CHILD_OK.",
    }
    result: dict[str, Any] = {}
    for name in required:
        if name in known:
            result[name] = known[name]
            continue
        kind = properties.get(name, {}).get("type")
        result[name] = {
            "boolean": False,
            "number": 1,
            "integer": 1,
            "array": [],
            "object": {},
        }.get(kind, "rehearsal")
    return result


def _user_text(messages: list[Any]) -> str:
    values: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            values.append(content)
        elif isinstance(content, list):
            values.extend(
                str(part.get("text"))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
    return "\n".join(values)


def _message_tool_names(messages: list[Any]) -> set[str]:
    result: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        tool_calls = message.get("tool_calls", [])
        if not isinstance(tool_calls, list):
            continue
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                result.add(function["name"])
    return result


def _usage(completion_tokens: int) -> dict[str, Any]:
    return {
        "prompt_tokens": 10,
        "completion_tokens": completion_tokens,
        "total_tokens": 10 + completion_tokens,
        "prompt_tokens_details": {"cached_tokens": 0},
    }


def _stream(chunks: tuple[dict[str, Any], ...], provider: str) -> UpstreamStream:
    body = "".join(
        f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"
        for chunk in chunks
    ) + "data: [DONE]\n\n"
    return UpstreamStream(
        status=200,
        headers={"Content-Type": "text/event-stream"},
        chunks=(body.encode("utf-8"),),
        provider_name=provider,
    )
