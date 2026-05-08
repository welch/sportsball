from unittest.mock import MagicMock

import pytest

from sportsball import stats


@pytest.fixture(autouse=True)
def _reset_stats() -> None:
    stats.reset()


def test_record_adapter_success_then_failure_keeps_both() -> None:
    stats.record_adapter_success("giants.fetch_events", 42)
    stats.record_adapter_failure("giants.fetch_events", "RuntimeError: boom")
    [snap] = stats.adapter_stats(["giants.fetch_events"])
    assert snap.last_success_at is not None
    assert snap.last_event_count == 42
    assert snap.last_failure_at is not None
    assert snap.last_error == "RuntimeError: boom"


def test_record_adapter_failure_then_success_keeps_both() -> None:
    stats.record_adapter_failure("warriors.fetch_events", "ConnectionError: nope")
    stats.record_adapter_success("warriors.fetch_events", 7)
    [snap] = stats.adapter_stats(["warriors.fetch_events"])
    assert snap.last_event_count == 7
    assert snap.last_error == "ConnectionError: nope"
    assert snap.last_failure_at is not None
    assert snap.last_success_at is not None


def test_adapter_stats_returns_empty_for_unknown_names() -> None:
    [a, b] = stats.adapter_stats(["never.ran", "also.never"])
    assert a.name == "never.ran"
    assert a.last_success_at is None
    assert a.last_failure_at is None
    assert b.name == "also.never"


def test_adapter_stats_preserves_requested_order() -> None:
    stats.record_adapter_success("b", 1)
    stats.record_adapter_success("a", 2)
    snaps = stats.adapter_stats(["a", "b", "c"])
    assert [s.name for s in snaps] == ["a", "b", "c"]


def test_snapshot_and_load_round_trip() -> None:
    stats.record_adapter_success("giants.fetch_events", 50)
    stats.record_adapter_failure("warriors.fetch_events", "boom")
    snapshot = stats.snapshot_adapter_stats()

    stats.reset()
    assert stats.adapter_stats(["giants.fetch_events"])[0].last_success_at is None

    stats.load_adapter_stats(snapshot)
    [g, w] = stats.adapter_stats(["giants.fetch_events", "warriors.fetch_events"])
    assert g.last_event_count == 50
    assert g.last_success_at is not None
    assert w.last_error == "boom"
    assert w.last_failure_at is not None


def _http_entry(status: int) -> MagicMock:
    entry = MagicMock()
    entry.http_request = {"status": status}
    return entry


def test_request_summary_aggregates_cloud_logging_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.list_entries.return_value = [
        _http_entry(200),
        _http_entry(204),
        _http_entry(301),
        _http_entry(404),
        _http_entry(500),
    ]
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    summary = stats.request_summary()
    assert summary.source == "cloud-logging"
    assert summary.total == 5
    assert summary.by_class["2xx"] == 2
    assert summary.by_class["3xx"] == 1
    assert summary.by_class["4xx"] == 1
    assert summary.by_class["5xx"] == 1
    # user_traffic counts 2xx only (real page views).
    assert summary.user_traffic == 2


def test_request_summary_skips_entries_without_http_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare = MagicMock()
    bare.http_request = None
    no_status = MagicMock()
    no_status.http_request = {}
    fake_client = MagicMock()
    fake_client.list_entries.return_value = [bare, no_status, _http_entry(200)]
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    summary = stats.request_summary()
    assert summary.total == 1
    assert summary.by_class["2xx"] == 1


def test_request_summary_caches_query_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def fake_query(window_hours: int = 24) -> stats.RequestSummary:
        calls["n"] += 1
        return stats.RequestSummary(total=1, by_class={"2xx": 1})

    monkeypatch.setattr(stats, "_query_request_summary", fake_query)
    stats.request_summary()
    stats.request_summary()
    stats.request_summary()
    assert calls["n"] == 1


def test_request_summary_returns_unavailable_on_query_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("API down")

    monkeypatch.setattr(stats, "_logging_client", boom)
    summary = stats.request_summary()
    assert summary.source == "unavailable"
    assert summary.total == 0


def test_query_filter_excludes_operator_and_cron_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Cloud Logging filter must skip /health/, /healthz, and
    /tasks/refresh so the user-traffic count isn't inflated by operator
    page reloads, GAE health probes, or cron invocations.
    """
    fake_client = MagicMock()
    fake_client.list_entries.return_value = []
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    stats._query_request_summary()
    call_filter = fake_client.list_entries.call_args.kwargs["filter_"]
    assert "/health/" in call_filter
    assert "/healthz" in call_filter
    assert "/tasks/refresh" in call_filter
    # All three are NOT clauses, not positive matches.
    for path in ("/health/", "/healthz", "/tasks/refresh"):
        idx = call_filter.find(path)
        assert "NOT" in call_filter[max(0, idx - 80) : idx], (
            f"path {path!r} not under a NOT clause in filter:\n{call_filter}"
        )
