from datetime import datetime
from zoneinfo import ZoneInfo

from sportsball.aggregator import PT, compute_status, fetch_all
from sportsball.models import Event

UTC = ZoneInfo("UTC")


def _ev(name: str, venue: str, when_utc: str, source: str = "test", source_id: str = "x") -> Event:
    return Event(
        source=source,
        source_id=source_id,
        name=name,
        starts_at=datetime.fromisoformat(when_utc),
        venue=venue,
    )


def test_fetch_all_filters_to_tracked_venues() -> None:
    def adapter_a() -> list[Event]:
        return [_ev("Giants at home", "Oracle Park", "2026-05-04T19:00:00+00:00", "g", "1")]

    def adapter_b() -> list[Event]:
        return [
            _ev("Giants at LA", "Dodger Stadium", "2026-05-04T19:00:00+00:00", "g", "2"),
            _ev("Warriors at home", "Chase Center", "2026-05-04T19:00:00+00:00", "w", "1"),
        ]

    events = fetch_all([adapter_a, adapter_b])
    assert {e.venue for e in events} == {"Oracle Park", "Chase Center"}
    assert len(events) == 2


def test_compute_status_no_events_today_no_future() -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=PT)
    s = compute_status([], now)
    assert s.today_events == []
    assert s.next_event_date is None
    assert s.next_event_events == []


def test_compute_status_today_only() -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=PT)
    # 7:05 PM PT = 02:05 UTC next day
    e = _ev("Mets at Giants", "Oracle Park", "2026-05-05T02:05:00+00:00")
    s = compute_status([e], now)
    assert s.today_events == [e]
    assert s.next_event_date is None


def test_compute_status_today_events_sorted_by_time() -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=PT)
    late = _ev("late", "Oracle Park", "2026-05-05T03:00:00+00:00", "x", "late")
    early = _ev("early", "Chase Center", "2026-05-05T01:00:00+00:00", "x", "early")
    s = compute_status([late, early], now)
    assert [e.source_id for e in s.today_events] == ["early", "late"]


def test_compute_status_next_event_only() -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=PT)
    e = _ev("Phillies at Giants", "Oracle Park", "2026-05-08T02:05:00+00:00")
    s = compute_status([e], now)
    assert s.today_events == []
    assert s.next_event_date == datetime(2026, 5, 7).date()
    assert s.next_event_events == [e]


def test_compute_status_picks_earliest_future_date_with_all_events_that_day() -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=PT)
    soon_a = _ev("Giants game", "Oracle Park", "2026-05-08T02:05:00+00:00", "g", "a")
    soon_b = _ev("Concert", "Chase Center", "2026-05-08T03:00:00+00:00", "c", "b")
    later = _ev("Later game", "Oracle Park", "2026-05-15T02:05:00+00:00", "g", "c")
    s = compute_status([later, soon_a, soon_b], now)
    assert s.next_event_date == datetime(2026, 5, 7).date()
    assert {e.source_id for e in s.next_event_events} == {"a", "b"}


def test_compute_status_ignores_past_events() -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=PT)
    past = _ev("yesterday", "Oracle Park", "2026-05-04T03:00:00+00:00")
    s = compute_status([past], now)
    assert s.today_events == []
    assert s.next_event_date is None


def test_next_quiet_date_no_events_today_is_today() -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=PT)
    s = compute_status([], now)
    assert s.next_quiet_date == datetime(2026, 5, 4).date()


def test_next_quiet_date_today_event_only() -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=PT)
    today_evt = _ev("Mets at Giants", "Oracle Park", "2026-05-05T02:05:00+00:00")
    s = compute_status([today_evt], now)
    # Tomorrow (5/5 PT) has no event in our list.
    assert s.next_quiet_date == datetime(2026, 5, 5).date()


def test_next_quiet_date_skips_consecutive_event_days() -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=PT)
    today_evt = _ev("today", "Oracle Park", "2026-05-05T02:05:00+00:00", "g", "1")
    tomorrow_evt = _ev("tomorrow", "Oracle Park", "2026-05-06T02:05:00+00:00", "g", "2")
    day_after = _ev("day after", "Chase Center", "2026-05-07T02:05:00+00:00", "w", "3")
    s = compute_status([today_evt, tomorrow_evt, day_after], now)
    # Events fall on 5/4, 5/5, 5/6 PT; first quiet day is 5/7.
    assert s.next_quiet_date == datetime(2026, 5, 7).date()
