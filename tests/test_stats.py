import time
from datetime import UTC, datetime
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

    def fake_query(window_minutes: int = 1440) -> stats.RequestSummary:
        calls["n"] += 1
        return stats.RequestSummary(total=1, by_class={"2xx": 1})

    monkeypatch.setattr(stats, "_query_request_summary", fake_query)
    stats.request_summary()
    stats.request_summary()
    stats.request_summary()
    assert calls["n"] == 1


# --- Two windows -------------------------------------------------------
#
# Surges observed in Cloud Monitoring run about fifteen minutes. A 24-hour
# count can't show one: by the time a spike is a visible fraction of a day's
# traffic it is long over. The half-hour column is what the operator reads
# after an alert email lands.


def test_each_window_caches_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """One shared cache slot would have the half-hour column serve the day's
    numbers — the two would agree exactly, and always."""
    asked: list[int] = []

    def fake_query(window_minutes: int = 1440) -> stats.RequestSummary:
        asked.append(window_minutes)
        return stats.RequestSummary(total=window_minutes, window_minutes=window_minutes)

    monkeypatch.setattr(stats, "_query_request_summary", fake_query)
    day = stats.request_summary()
    recent = stats.request_summary(window_minutes=stats.RECENT_WINDOW_MINUTES)
    assert asked == [1440, 30]
    assert day.total == 1440
    assert recent.total == 30
    # Second time round, both come from cache.
    stats.request_summary()
    stats.request_summary(window_minutes=stats.RECENT_WINDOW_MINUTES)
    assert asked == [1440, 30]


def test_short_window_expires_from_cache_sooner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Five minutes of staleness is a sixth of a half hour. The column exists
    to move; a day-length TTL would freeze it."""
    assert stats._ttl_seconds(stats.RECENT_WINDOW_MINUTES) < stats._ttl_seconds(
        stats.DEFAULT_WINDOW_MINUTES
    )


def test_monitoring_window_is_measured_in_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 30-minute window asked for in whole hours rounds to 0 or 60 — either
    an empty column or one silently counting twice its own span."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    client = _fake_monitoring(_series("200", 5))
    monkeypatch.setattr(stats, "_monitoring_client", lambda: client)

    summary = stats.request_summary(window_minutes=30)
    request = client.list_time_series.call_args.kwargs["request"]
    assert request["aggregation"].alignment_period.seconds == 1800
    interval = request["interval"]
    assert (interval.end_time - interval.start_time).total_seconds() == 1800
    assert summary.window_minutes == 30
    assert summary.window_label == "30 min"


def test_traffic_rows_union_codes_across_both_windows() -> None:
    """A code seen only in the last half hour is exactly the interesting one —
    it must not be dropped because the day column has no entry for it."""
    day = stats.RequestSummary(
        total=100, by_code={"200": 90, "404": 10}, by_class={"2xx": 90, "4xx": 10}
    )
    recent = stats.RequestSummary(
        total=7, by_code={"200": 2, "500": 5}, by_class={"2xx": 2, "5xx": 5}
    )
    rows = stats.traffic_rows(day, recent)
    assert [r.label for r in rows] == ["200", "404", "500"]
    by_label = {r.label: r for r in rows}
    assert by_label["500"].count == 0
    assert by_label["500"].recent == 5
    assert by_label["404"].recent == 0
    assert by_label["200"].share == 0.9
    assert by_label["200"].served is True
    assert by_label["404"].served is False


def test_traffic_rows_fall_back_to_classes_for_both_columns() -> None:
    """Mixing an exact-code column with a class column would put 404 and 4xx
    on the same line and invite reading across them."""
    day = stats.RequestSummary(total=10, by_class={"2xx": 10})
    recent = stats.RequestSummary(total=3, by_code={"200": 3}, by_class={"2xx": 3})
    rows = stats.traffic_rows(day, recent)
    assert [r.label for r in rows] == ["2xx", "3xx", "4xx", "5xx"]
    assert rows[0].count == 10
    assert rows[0].recent == 3


def test_traffic_rows_share_is_none_when_nothing_was_served() -> None:
    """A quiet window must not divide by zero on the way to the page."""
    empty = stats.RequestSummary()
    rows = stats.traffic_rows(empty, empty)
    assert all(r.share is None for r in rows)


# --- Page views by domain -----------------------------------------------
#
# One deployment answers to several domains, and the response_count metric
# carries no host label — only `loading` and `response_code`. The request log
# is the only source, and it has no server-side count.
#
# Restricting to 2xx is what makes counting the whole window affordable: barely
# 630 of ~8,700 daily requests are 2xx. The first version instead sampled all
# requests a fixed 1,000 lines deep, which reached back about two hours and, in
# a quiet stretch, reported the two sites evenly matched on 15 page views
# against 13 — when the day's real figures were 546 and 82.


def _host_entry(host: str) -> MagicMock:
    entry = MagicMock()
    entry.payload = {"host": host}
    entry.timestamp = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    return entry


def test_host_split_counts_only_successful_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2xx restriction lives in the log filter, not in Python. Counting
    client-side would mean paging through every crawler 404 to find the few
    hundred entries worth having — the whole cost this avoids."""
    fake_client = MagicMock()
    fake_client.list_entries.return_value = [_host_entry("ismydayfucked.com")]
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    stats.host_split(["ismydayfucked.com"])
    call_filter = fake_client.list_entries.call_args.kwargs["filter_"]
    assert "httpRequest.status>=200" in call_filter
    assert "httpRequest.status<300" in call_filter


def test_host_split_folds_www_onto_the_apex(monkeypatch: pytest.MonkeyPatch) -> None:
    """`www.` 301s to the apex before rendering anything, so counting it
    separately would split one site's traffic across two rows."""
    fake_client = MagicMock()
    fake_client.list_entries.return_value = [
        _host_entry("ismydayfucked.com"),
        _host_entry("www.ismydayfucked.com"),
        _host_entry("ismydayhosed.fun"),
    ]
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    split = stats.host_split(["ismydayfucked.com", "ismydayhosed.fun"])
    assert split.page_views == 3
    assert [(s.host, s.page_views) for s in split.shares] == [
        ("ismydayfucked.com", 2),
        ("ismydayhosed.fun", 1),
    ]
    assert abs(split.shares[0].share - 2 / 3) < 1e-9


def test_host_split_pools_unconfigured_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    """appspot.com alone would take second place on a table about which of the
    two sites people actually visit."""
    fake_client = MagicMock()
    fake_client.list_entries.return_value = [
        _host_entry("sports-ball.appspot.com"),
        _host_entry("sports-ball.appspot.com"),
        _host_entry("34.117.0.1"),
        _host_entry("ismydayfucked.com"),
    ]
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    split = stats.host_split(["ismydayfucked.com", "ismydayhosed.fun"])
    assert [(s.host, s.page_views) for s in split.shares] == [
        ("ismydayfucked.com", 1),
        ("other", 3),
    ]


def test_host_split_covers_the_whole_window_when_it_can(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not truncated means the counts are the window's real totals, and the
    heading may name the window rather than the span it happened to reach."""
    fake_client = MagicMock()
    fake_client.list_entries.return_value = [_host_entry("ismydayfucked.com")]
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    split = stats.host_split(["ismydayfucked.com"])
    assert split.truncated is False
    assert split.window_minutes == stats.DEFAULT_WINDOW_MINUTES
    assert split.window_label == "24 hours"
    assert "timestamp>=" in fake_client.list_entries.call_args.kwargs["filter_"]


def test_host_split_keeps_configured_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured order first, pool last — so the two rows worth comparing sit
    next to each other however the counts came out."""
    fake_client = MagicMock()
    fake_client.list_entries.return_value = [
        _host_entry("elsewhere.example"),
        _host_entry("ismydayhosed.fun"),
        _host_entry("ismydayhosed.fun"),
        _host_entry("ismydayfucked.com"),
    ]
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    split = stats.host_split(["ismydayfucked.com", "ismydayhosed.fun"])
    assert [s.host for s in split.shares] == ["ismydayfucked.com", "ismydayhosed.fun", "other"]


def test_host_split_flags_a_truncated_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Past the cap the counts cover less than the window, and saying "last 24
    hours" over a number that covers two would be the same mistake in a new
    place — a partial count reported as a whole one."""
    monkeypatch.setattr(stats, "_HOST_MAX_ENTRIES", 5)
    fake_client = MagicMock()
    fake_client.list_entries.return_value = (
        _host_entry("ismydayfucked.com") for _ in range(10_000)
    )
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    split = stats.host_split(["ismydayfucked.com"])
    assert split.page_views == 5
    assert split.truncated is True
    # The span actually covered, for the heading to name instead.
    assert split.since is not None


def test_host_split_samples_the_newest_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """`list_entries` defaults to ascending. Left at the default, a walk of the
    window starts at the far end of the retention period — which on the first
    attempt returned an empty split, because entries that old had aged out."""
    fake_client = MagicMock()
    fake_client.list_entries.return_value = [_host_entry("ismydayfucked.com")]
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    stats.host_split(["ismydayfucked.com"])
    kwargs = fake_client.list_entries.call_args.kwargs
    assert kwargs["order_by"] == "timestamp desc"
    # And bounded below, so the backend isn't offered the whole retention window.
    assert "timestamp>=" in kwargs["filter_"]


def test_host_split_is_unavailable_when_the_log_query_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> object:
        raise RuntimeError("no logging")

    monkeypatch.setattr(stats, "_logging_client", boom)
    split = stats.host_split(["ismydayfucked.com"])
    assert split.available is False
    assert split.shares == []


def test_host_split_is_unavailable_when_nothing_carried_a_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A split of zero requests renders as real zeros, which is worse than
    saying nothing."""
    bare = MagicMock()
    bare.payload = None
    fake_client = MagicMock()
    fake_client.list_entries.return_value = [bare, bare]
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    split = stats.host_split(["ismydayfucked.com"])
    assert split.available is False


def test_host_split_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = MagicMock()
    fake_client.list_entries.return_value = [_host_entry("ismydayfucked.com")]
    monkeypatch.setattr(stats, "_logging_client", lambda: fake_client)

    stats.host_split(["ismydayfucked.com"])
    stats.host_split(["ismydayfucked.com"])
    assert fake_client.list_entries.call_count == 1


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
