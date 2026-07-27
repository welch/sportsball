from datetime import date, datetime

from sportsball.aggregator import day_halos, month_view
from sportsball.models import Event


def _ev(
    name: str,
    venue: str,
    when_utc: str,
    kind: str = "home",
    source_id: str = "x",
) -> Event:
    return Event(
        source="test",
        source_id=source_id,
        name=name,
        starts_at=datetime.fromisoformat(when_utc),
        venue=venue,
        kind=kind,  # type: ignore[arg-type]
    )


def test_day_halos_empty() -> None:
    assert day_halos([]) == []


def test_day_halos_hue_is_venue_texture_is_kind() -> None:
    """Two channels: color says where, solid-vs-dashed says what."""
    giants = _ev("Mets at Giants", "Oracle Park", "2026-05-05T02:05:00+00:00")
    warriors = _ev("Warriors vs Lakers", "Chase Center", "2026-05-05T02:30:00+00:00")
    oracle_show = _ev("Noah Kahan", "Oracle Park", "2026-05-05T03:00:00+00:00", "event")
    chase_show = _ev("Some Band", "Chase Center", "2026-05-05T03:00:00+00:00", "event")
    assert day_halos([giants]) == ["halo-giants"]
    assert day_halos([warriors]) == ["halo-warriors"]
    assert day_halos([oracle_show]) == ["ring-oracle"]
    assert day_halos([chase_show]) == ["ring-chase"]


def test_day_halos_ordered_innermost_first() -> None:
    """CSS draws the marks in list order, so the sequence is load-bearing."""
    chase_show = _ev("Band", "Chase Center", "2026-05-05T03:00:00+00:00", "event", "1")
    warriors = _ev("Warriors", "Chase Center", "2026-05-05T02:30:00+00:00", "home", "2")
    giants = _ev("Giants", "Oracle Park", "2026-05-05T02:05:00+00:00", "home", "3")
    oracle_show = _ev("Kahan", "Oracle Park", "2026-05-05T04:00:00+00:00", "event", "4")
    # Input order shuffled; output order must not be.
    assert day_halos([chase_show, warriors, giants, oracle_show]) == [
        "halo-giants",
        "ring-oracle",
        "halo-warriors",
        "ring-chase",
    ]


def test_day_halos_non_team_event_never_borrows_the_home_glow() -> None:
    """The monster-truck case: a non-team event at Chase Center gets the
    venue's hue but must never wear the Warriors' solid glow."""
    trucks = _ev("Hot Wheels Monster Trucks", "Chase Center", "2026-05-05T03:00:00+00:00", "event")
    assert day_halos([trucks]) == ["ring-chase"]
    kahan = _ev("Noah Kahan", "Oracle Park", "2026-05-05T03:00:00+00:00", "event")
    assert day_halos([kahan]) == ["ring-oracle"]


def test_day_halos_same_venue_both_kinds_compose() -> None:
    game = _ev("Mets at Giants", "Oracle Park", "2026-05-05T02:05:00+00:00", "home", "1")
    show = _ev("Noah Kahan", "Oracle Park", "2026-05-05T03:00:00+00:00", "event", "2")
    assert day_halos([game, show]) == ["halo-giants", "ring-oracle"]


def test_month_view_covers_whole_weeks_sunday_first() -> None:
    view = month_view([], date(2026, 5, 1), date(2026, 5, 4))
    assert all(len(week) == 7 for week in view.weeks)
    assert all(week[0].day.weekday() == 6 for week in view.weeks)  # Sunday
    # May 2026 starts on a Friday, so the grid opens on Sun Apr 26.
    assert view.weeks[0][0].day == date(2026, 4, 26)
    assert view.weeks[-1][-1].day >= date(2026, 5, 31)


def test_month_view_marks_in_month_and_today() -> None:
    view = month_view([], date(2026, 5, 1), date(2026, 5, 4))
    cells = {c.day: c for week in view.weeks for c in week}
    assert cells[date(2026, 5, 4)].in_month is True
    assert cells[date(2026, 5, 4)].is_today is True
    assert cells[date(2026, 4, 26)].in_month is False
    assert cells[date(2026, 4, 26)].is_today is False


def test_month_view_places_events_on_pacific_local_date() -> None:
    """A 7:05pm PT first pitch is the *next* day in UTC — it belongs to the
    day the neighbors actually lose their parking to."""
    late = _ev("Mets at Giants", "Oracle Park", "2026-05-05T02:05:00+00:00")
    view = month_view([late], date(2026, 5, 1), date(2026, 5, 4))
    cells = {c.day: c for week in view.weeks for c in week}
    assert len(cells[date(2026, 5, 4)].events) == 1
    assert cells[date(2026, 5, 4)].halos == ["halo-giants"]
    assert cells[date(2026, 5, 5)].events == []


def test_month_view_adjacent_month_days_keep_their_events() -> None:
    """Spill days are dimmed, not blanked — a game on Apr 30 still shows
    when you're looking at May."""
    spill = _ev("Warriors", "Chase Center", "2026-05-01T02:30:00+00:00")
    view = month_view([spill], date(2026, 5, 1), date(2026, 5, 4))
    cells = {c.day: c for week in view.weeks for c in week}
    cell = cells[date(2026, 4, 30)]
    assert cell.in_month is False
    assert cell.halos == ["halo-warriors"]


def test_month_view_sorts_a_days_events_by_start() -> None:
    late = _ev("late", "Chase Center", "2026-05-05T03:30:00+00:00", "event", "1")
    early = _ev("early", "Oracle Park", "2026-05-05T02:05:00+00:00", "home", "2")
    view = month_view([late, early], date(2026, 5, 1), date(2026, 5, 4))
    cells = {c.day: c for week in view.weeks for c in week}
    assert [e.name for e in cells[date(2026, 5, 4)].events] == ["early", "late"]


def test_month_view_prev_next_within_year() -> None:
    view = month_view([], date(2026, 5, 1), date(2026, 5, 4))
    assert view.prev_month == date(2026, 4, 1)
    assert view.next_month == date(2026, 6, 1)


def test_month_view_prev_wraps_january_to_december() -> None:
    view = month_view([], date(2026, 1, 1), date(2026, 1, 15))
    assert view.prev_month == date(2025, 12, 1)
    assert view.next_month == date(2026, 2, 1)


def test_month_view_next_wraps_december_to_january() -> None:
    view = month_view([], date(2026, 12, 1), date(2026, 12, 15))
    assert view.prev_month == date(2026, 11, 1)
    assert view.next_month == date(2027, 1, 1)


def test_month_view_february_leap_year() -> None:
    view = month_view([], date(2028, 2, 1), date(2028, 2, 10))
    in_month = [c.day for week in view.weeks for c in week if c.in_month]
    assert in_month[-1] == date(2028, 2, 29)


def test_month_view_no_today_marker_outside_the_month() -> None:
    view = month_view([], date(2026, 8, 1), date(2026, 5, 4))
    assert not any(c.is_today for week in view.weeks for c in week)


def test_month_view_ignores_events_in_other_months() -> None:
    far = _ev("December game", "Oracle Park", "2026-12-05T02:05:00+00:00")
    view = month_view([far], date(2026, 5, 1), date(2026, 5, 4))
    assert all(c.events == [] for week in view.weeks for c in week)
