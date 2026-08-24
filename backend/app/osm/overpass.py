from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from typing import Any

from app.config import DEFAULT_MIRRORS, USER_AGENT

PostFn = Callable[[str, str], bytes]
"""Takes (mirror_url, overpass_ql) and returns the raw response body."""


class OverpassError(RuntimeError):
    pass


def _httpx_post(url: str, ql: str, timeout: float) -> bytes:
    import httpx

    response = httpx.post(
        url,
        data={"data": ql},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.content


class OverpassClient:
    """Queries Overpass, rotating mirrors with exponential backoff.

    The public instances routinely return 429/500/502/504 under load, so a
    single attempt against a single mirror is not good enough even for the
    one-off district extract.
    """

    def __init__(
        self,
        mirrors: Sequence[str] = DEFAULT_MIRRORS,
        max_attempts: int = 8,
        timeout_s: float = 300.0,
        base_backoff_s: float = 5.0,
        post: PostFn | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_attempt: Callable[[int, str, str], None] | None = None,
    ) -> None:
        if not mirrors:
            raise ValueError("at least one mirror is required")
        self.mirrors = tuple(mirrors)
        self.max_attempts = max_attempts
        self.timeout_s = timeout_s
        self.base_backoff_s = base_backoff_s
        self._post = post or (lambda url, ql: _httpx_post(url, ql, timeout_s))
        self._sleep = sleep
        self._on_attempt = on_attempt

    def query_raw(self, ql: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            mirror = self.mirrors[attempt % len(self.mirrors)]
            try:
                body = self._post(mirror, ql)
            except Exception as exc:  # noqa: BLE001 - any transport failure is retryable
                last_error = exc
                self._note(attempt, mirror, f"failed: {exc}")
                if attempt < self.max_attempts - 1:
                    self._sleep(self.base_backoff_s * (attempt + 1))
                continue
            self._note(attempt, mirror, f"ok ({len(body) / 1e6:.1f} MB)")
            return body
        raise OverpassError(
            f"all {self.max_attempts} attempts across {len(self.mirrors)} mirrors failed; "
            f"last error: {last_error}"
        ) from last_error

    def query(self, ql: str) -> dict[str, Any]:
        return json.loads(self.query_raw(ql).decode())

    def elements(self, ql: str) -> list[dict[str, Any]]:
        return self.query(ql).get("elements", [])

    def _note(self, attempt: int, mirror: str, message: str) -> None:
        if self._on_attempt:
            self._on_attempt(attempt, mirror, message)
