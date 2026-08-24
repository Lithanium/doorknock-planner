from __future__ import annotations

import pytest

from app.osm.overpass import OverpassClient, OverpassError

MIRRORS = ["https://a/api", "https://b/api", "https://c/api"]


def make_client(post, **kwargs):
    slept: list[float] = []
    client = OverpassClient(
        mirrors=MIRRORS,
        post=post,
        sleep=slept.append,
        base_backoff_s=1.0,
        **kwargs,
    )
    return client, slept


def test_returns_body_on_first_success():
    calls = []

    def post(url, ql):
        calls.append(url)
        return b'{"elements": [{"id": 1}]}'

    client, slept = make_client(post)
    assert client.elements("test") == [{"id": 1}]
    assert calls == [MIRRORS[0]]
    assert slept == []


def test_rotates_mirrors_and_backs_off_before_succeeding():
    calls = []

    def post(url, ql):
        calls.append(url)
        if len(calls) < 3:
            raise RuntimeError("503 Service Unavailable")
        return b'{"elements": []}'

    client, slept = make_client(post)
    assert client.elements("test") == []
    assert calls == [MIRRORS[0], MIRRORS[1], MIRRORS[2]]
    assert slept == [1.0, 2.0]


def test_raises_after_exhausting_attempts_and_reports_last_error():
    def post(url, ql):
        raise RuntimeError("429 Too Many Requests")

    client, slept = make_client(post, max_attempts=4)
    with pytest.raises(OverpassError, match="429 Too Many Requests"):
        client.query("test")
    assert len(slept) == 3


def test_does_not_sleep_after_the_final_failed_attempt():
    def post(url, ql):
        raise RuntimeError("boom")

    client, slept = make_client(post, max_attempts=1)
    with pytest.raises(OverpassError):
        client.query("test")
    assert slept == []


def test_reports_progress_for_each_attempt():
    notes = []

    def post(url, ql):
        if not notes:
            raise RuntimeError("504")
        return b"{}"

    client = OverpassClient(
        mirrors=MIRRORS,
        post=post,
        sleep=lambda _s: None,
        on_attempt=lambda attempt, mirror, msg: notes.append((attempt, mirror, msg)),
    )
    client.query("test")
    assert len(notes) == 2
    assert "failed" in notes[0][2]
    assert "ok" in notes[1][2]


def test_requires_at_least_one_mirror():
    with pytest.raises(ValueError):
        OverpassClient(mirrors=[])
