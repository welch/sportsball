from datetime import datetime, timedelta

import pytest

from sportsball import stats
from sportsball.aggregator import PT


@pytest.fixture(autouse=True)
def _reset_stats() -> None:
    stats.reset()


def test_record_request_increments_total_and_class() -> None:
    stats.record_request(200)
    stats.record_request(204)
    stats.record_request(404)
    summary = stats.request_summary()
    assert summary.total == 3
    assert summary.by_class["2xx"] == 2
    assert summary.by_class["4xx"] == 1
    assert summary.by_class.get("5xx", 0) == 0


def test_request_summary_prunes_entries_older_than_24h(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = datetime(2026, 5, 4, 12, 0, tzinfo=PT)
    times = iter(
        [
            base - timedelta(hours=25),  # too old, should drop
            base - timedelta(hours=23, minutes=59),  # in window
            base,  # in window
            base,  # read time
        ]
    )
    monkeypatch.setattr(stats, "_now", lambda: next(times))
    stats.record_request(200)  # too old
    stats.record_request(500)  # in window
    stats.record_request(301)  # in window
    summary = stats.request_summary()
    assert summary.total == 2
    assert summary.by_class.get("2xx", 0) == 0
    assert summary.by_class["3xx"] == 1
    assert summary.by_class["5xx"] == 1


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

    # Simulate a fresh process by clearing in-memory state, then loading.
    stats.reset()
    assert stats.adapter_stats(["giants.fetch_events"])[0].last_success_at is None

    stats.load_adapter_stats(snapshot)
    [g, w] = stats.adapter_stats(["giants.fetch_events", "warriors.fetch_events"])
    assert g.last_event_count == 50
    assert g.last_success_at is not None
    assert w.last_error == "boom"
    assert w.last_failure_at is not None


def test_load_adapter_stats_does_not_clear_request_window() -> None:
    """Per-process traffic data must survive when adapter snapshot is loaded
    from a remote-cron's state."""
    stats.record_request(200)
    snap = [stats.AdapterStats(name="giants.fetch_events", last_event_count=10)]
    stats.load_adapter_stats(snap)
    assert stats.request_summary().total == 1
