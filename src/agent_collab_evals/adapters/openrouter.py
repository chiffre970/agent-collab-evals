"""Pinned OpenRouter streaming transport with generation-receipt capture."""

from __future__ import annotations

import http.client
import time
from collections.abc import Callable, Iterable
from typing import Protocol
from urllib.parse import quote, urlsplit

from ..model_gateway import (
    ModelGatewayProfile,
    UpstreamRequestRejected,
    UpstreamStream,
)


class _Response(Protocol):
    status: int

    def getheaders(self) -> list[tuple[str, str]]: ...

    def getheader(self, name: str, default: str | None = None) -> str | None: ...

    def read(self, amount: int | None = None) -> bytes: ...

    def read1(self, amount: int = -1) -> bytes: ...

    def close(self) -> None: ...


class _Connection(Protocol):
    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None: ...

    def getresponse(self) -> _Response: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[str, int, float], _Connection]


class OpenRouterUpstream:
    """Send one pinned request and make its generation receipt retrievable."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        app_title: str,
        *,
        timeout_seconds: float = 120.0,
        chunk_bytes: int = 64 * 1024,
        max_error_bytes: int = 1024 * 1024,
        max_metadata_bytes: int = 1024 * 1024,
        metadata_retry_seconds: tuple[float, ...] = (0, 0.5, 1, 2, 4, 8),
        connection_factory: ConnectionFactory | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "OpenRouter endpoint must be an HTTPS URL without credentials, "
                "query or fragment"
            )
        if not api_key.strip() or not app_title.strip():
            raise ValueError("OpenRouter API key and application title are required")
        if timeout_seconds <= 0 or chunk_bytes < 1:
            raise ValueError("OpenRouter transport bounds must be positive")
        if max_error_bytes < 1 or max_metadata_bytes < 1:
            raise ValueError("OpenRouter receipt bounds must be positive")
        if not metadata_retry_seconds or any(
            delay < 0 for delay in metadata_retry_seconds
        ):
            raise ValueError("OpenRouter metadata retries must be nonnegative")
        self._host = parsed.hostname
        self._port = parsed.port or 443
        self._base_path = parsed.path.rstrip("/")
        self._api_key = api_key
        self._app_title = app_title
        self._timeout_seconds = timeout_seconds
        self._chunk_bytes = chunk_bytes
        self._max_error_bytes = max_error_bytes
        self._max_metadata_bytes = max_metadata_bytes
        self._metadata_retry_seconds = metadata_retry_seconds
        self._connection_factory = connection_factory or self._https_connection
        self._sleep = sleep

    @classmethod
    def from_profile(
        cls,
        profile: ModelGatewayProfile,
        api_key: str,
        **options: object,
    ) -> "OpenRouterUpstream":
        if profile.cache_policy != "disabled":
            raise ValueError(
                "OpenRouter V0 requires the response cache to be disabled"
            )
        return cls(
            profile.upstream_endpoint,
            api_key,
            profile.client_app_title,
            **options,
        )

    def stream(self, request: bytes) -> UpstreamStream:
        connection = self._connect()
        try:
            connection.request(
                "POST",
                f"{self._base_path}/chat/completions",
                body=request,
                headers=self._headers("text/event-stream"),
            )
            response = connection.getresponse()
        except Exception:
            connection.close()
            raise

        headers = {name: value for name, value in response.getheaders()}
        if response.status != 200:
            try:
                raw_error = self._bounded_read(response, self._max_error_bytes)
            finally:
                response.close()
                connection.close()
            raise UpstreamRequestRejected(
                response.status,
                raw_error,
                definitely_unstarted=400 <= response.status < 500,
            )

        generation_id = response.getheader("X-Generation-Id")
        state = {"complete": False}

        def chunks() -> Iterable[bytes]:
            try:
                while True:
                    chunk = response.read1(self._chunk_bytes)
                    if not chunk:
                        state["complete"] = True
                        return
                    yield chunk
            finally:
                response.close()
                connection.close()

        def metadata_receipt() -> bytes:
            if not state["complete"]:
                raise RuntimeError(
                    "OpenRouter stream did not complete before metadata collection"
                )
            if not generation_id:
                raise RuntimeError("OpenRouter response omitted X-Generation-Id")
            return self._fetch_generation(generation_id)

        return UpstreamStream(
            200,
            headers,
            chunks(),
            metadata_receipt=metadata_receipt,
        )

    def _fetch_generation(self, generation_id: str) -> bytes:
        last_status: int | None = None
        last_body = b""
        for delay in self._metadata_retry_seconds:
            if delay:
                self._sleep(delay)
            connection = self._connect()
            try:
                target = (
                    f"{self._base_path}/generation?id="
                    f"{quote(generation_id, safe='')}"
                )
                connection.request("GET", target, headers=self._headers("application/json"))
                response = connection.getresponse()
                try:
                    body = self._bounded_read(response, self._max_metadata_bytes)
                finally:
                    response.close()
                if response.status == 200:
                    return body
                last_status = response.status
                last_body = body
                if response.status not in {404, 429, 500, 502, 503, 524, 529}:
                    break
            finally:
                connection.close()
        detail = (
            f"HTTP {last_status}" if last_status is not None else "no response"
        )
        raise RuntimeError(
            f"OpenRouter generation metadata remained unavailable ({detail}); "
            f"receipt bytes={len(last_body)}"
        )

    def _headers(self, accept: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": accept,
            "X-Title": self._app_title,
            "X-OpenRouter-Cache": "false",
        }

    def _connect(self) -> _Connection:
        return self._connection_factory(
            self._host, self._port, self._timeout_seconds
        )

    @staticmethod
    def _https_connection(host: str, port: int, timeout: float) -> _Connection:
        return http.client.HTTPSConnection(host, port, timeout=timeout)

    @staticmethod
    def _bounded_read(response: _Response, maximum: int) -> bytes:
        body = response.read(maximum + 1)
        if len(body) > maximum:
            raise ValueError("OpenRouter response exceeded its configured bound")
        return body
