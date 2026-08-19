import time
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


# --- Cloud Monitoring, the primary source ------------------------------------
#
# The log scan counts entries in Python at ~2.75ms each, so its cost tracks the
# traffic it is measuring and it has to be bounded — which makes it least
# accurate exactly when the page is most interesting. Monitoring aggregates
# server-side: one call, exact numbers, same cost at any volume.


def _series(code: str, value: int) -> MagicMock:
    ts = MagicMock()
    ts.metric.labels = {"response_code": code}
    point = MagicMock()
    point.value.int64_value = value
    ts.points = [point]
    return ts


def _fake_monitoring(*series: MagicMock) -> MagicMock:
    client = MagicMock()
    client.list_time_series.return_value = list(series)
    return client


def test_monitoring_summary_buckets_exact_codes_into_classes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr(
        stats,
        "_monitoring_client",
        lambda: _fake_monitoring(
            _series("200", 448), _series("204", 11), _series("302", 2155), _series("404", 2937)
        ),
    )

    summary = stats.request_summary()
    assert summary.source == "cloud-monitoring"
    assert summary.truncated is False
    assert summary.total == 5551
    assert summary.by_class == {"2xx": 459, "3xx": 2155, "4xx": 2937}
    # Exact codes survive, which classes alone cannot express.
    assert summary.by_code["404"] == 2937
    assert summary.user_traffic == 459


def test_monitoring_summary_groups_by_response_code_not_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The App Engine metric labels series `response_code`. Asking for
    `response_code_class` — the name on the Cloud Run equivalent, and the one
    the original issue specified — returns a single unlabelled series, so every
    count silently collapses into one bucket."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    client = _fake_monitoring(_series("200", 1))
    monkeypatch.setattr(stats, "_monitoring_client", lambda: client)

    stats.request_summary()
    request = client.list_time_series.call_args.kwargs["request"]
    assert request["aggregation"].group_by_fields == ["metric.labels.response_code"]
    assert "response_count" in request["filter"]


def test_monitoring_summary_needs_a_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a project there is nothing to query; it must fall through to the
    scan rather than raising into the page."""
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    fake_client = MagicMock()
    fake_client.list_entries.return_value = [_http_entry(200)]
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    summary = stats.request_summary()
    assert summary.source == "cloud-logging"
    assert summary.total == 1


def test_summary_falls_back_to_the_scan_when_monitoring_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expected while the service account lacks roles/monitoring.viewer: a
    bounded floor with a warning beats no number at all."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")

    def boom() -> object:
        raise RuntimeError("permission denied")

    monkeypatch.setattr(stats, "_monitoring_client", boom)
    fake_client = MagicMock()
    fake_client.list_entries.return_value = [_http_entry(200), _http_entry(404)]
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    summary = stats.request_summary()
    assert summary.source == "cloud-logging"
    assert summary.total == 2
    assert summary.by_code == {}


def test_summary_is_unavailable_only_when_both_sources_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")

    def boom() -> object:
        raise RuntimeError("nope")

    monkeypatch.setattr(stats, "_monitoring_client", boom)
    monkeypatch.setattr(stats, "_logging_client", boom)

    summary = stats.request_summary()
    assert summary.source == "unavailable"
    assert summary.total == 0


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


# --- Scan bounds --------------------------------------------------------
#
# The scan used to be unbounded: it walked every matching log entry in the
# window at ~2.75ms each. A GPTBot flood pushed the window to 93k entries,
# the query to ~182s, and gunicorn killed the worker at its 30s timeout —
# so /health returned 500 rather than degrading. The endpoint's cost has to
# be bounded by construction, not by how much traffic the site happened to
# get.


def test_query_stops_at_the_entry_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stats, "_REQUEST_SCAN_MAX_ENTRIES", 10)
    fake_client = MagicMock()
    fake_client.list_entries.return_value = (_http_entry(200) for _ in range(10_000))
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    summary = stats._query_request_summary()
    assert summary.total == 10
    assert summary.truncated is True


def test_query_stops_at_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entry count alone isn't enough of a bound — a slow API can blow the
    budget well under the cap."""
    monkeypatch.setattr(stats, "_REQUEST_SCAN_DEADLINE_SECONDS", 0.05)

    def slow_entries() -> object:
        for _ in range(10_000):
            time.sleep(0.001)
            yield _http_entry(200)

    fake_client = MagicMock()
    fake_client.list_entries.return_value = slow_entries()
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    started = time.monotonic()
    summary = stats._query_request_summary()
    assert time.monotonic() - started < 5.0
    assert summary.truncated is True
    assert summary.total < 10_000


def test_untruncated_scan_is_not_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = MagicMock()
    fake_client.list_entries.return_value = [_http_entry(200), _http_entry(404)]
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    summary = stats.request_summary()
    assert summary.truncated is False
    assert summary.total == 2
