from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from sportsball import main
from sportsball.aggregator import PT
from sportsball.models import Event

UTC = ZoneInfo("UTC")


@pytest.fixture
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=PT)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
            return now if tz is None else now.astimezone(tz)  # type: ignore[arg-type]

    monkeypatch.setattr(main, "datetime", _Frozen)
    return now


def _ev(name: str, venue: str, when_utc: str) -> Event:
    return Event(
        source="test",
        source_id=name,
        name=name,
        starts_at=datetime.fromisoformat(when_utc),
        venue=venue,
    )


def test_healthz_responds() -> None:
    response = main.app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.data == b"ok"


def test_index_no_events(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/")
    assert response.status_code == 200
    assert b"All clear. No future events scheduled." in response.data
    assert b"8ball-no-1.gif" in response.data
    assert b"8ball-yes-1.gif" not in response.data
    assert b"halo-giants" not in response.data
    assert b"halo-warriors" not in response.data


def test_index_today_giants_event(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> None:
    today_event = _ev(
        "New York Mets at San Francisco Giants",
        "Oracle Park",
        "2026-05-05T02:05:00+00:00",
    )
    monkeypatch.setattr(main, "_events", lambda: [today_event])
    response = main.app.test_client().get("/")
    assert response.status_code == 200
    assert b"New York Mets at " in response.data
    assert b'class="giants">San Francisco Giants</span>' in response.data
    assert b"Oracle Park" in response.data
    assert b"7:05 PM" in response.data
    assert b"<time datetime=" in response.data
    assert b"8ball-yes-1.gif" in response.data
    assert b"halo-giants" in response.data
    assert b"halo-warriors" not in response.data
    assert b'class="verb giants"' in response.data


def test_index_today_both_venues_show_both_halos_and_neutral_verb(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    giants = _ev(
        "New York Mets at San Francisco Giants",
        "Oracle Park",
        "2026-05-05T02:05:00+00:00",
    )
    warriors = _ev(
        "Los Angeles Lakers at Golden State Warriors",
        "Chase Center",
        "2026-05-05T03:00:00+00:00",
    )
    monkeypatch.setattr(main, "_events", lambda: [giants, warriors])
    response = main.app.test_client().get("/")
    assert b"halo-giants" in response.data
    assert b"halo-warriors" in response.data
    # Both teams active → verb stays neutral, no team color class.
    assert b'class="verb giants"' not in response.data
    assert b'class="verb warriors"' not in response.data


def test_index_future_event(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> None:
    # 2026-05-09T02:00 UTC = 2026-05-08 19:00 PT (Friday), 4 days from Mon 5/4
    future = _ev(
        "Philadelphia Phillies at San Francisco Giants",
        "Oracle Park",
        "2026-05-09T02:00:00+00:00",
    )
    monkeypatch.setattr(main, "_events", lambda: [future])
    response = main.app.test_client().get("/")
    assert response.status_code == 200
    assert b"All clear until " in response.data
    assert b"Friday" in response.data
    assert b"Philadelphia Phillies at " in response.data
    assert b'class="giants">San Francisco Giants</span>' in response.data
    assert b"8ball-no-1.gif" in response.data
    # Verb color is only for today's events; not-fucked days have a neutral verb.
    assert b'class="verb giants"' not in response.data
    # No halo on future-event days either — bare 8-ball.
    assert b"halo-giants" not in response.data
    assert b"halo-warriors" not in response.data


def test_index_future_event_tomorrow(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> None:
    # 1 day out → "tomorrow"
    future = _ev(
        "Philadelphia Phillies at San Francisco Giants",
        "Oracle Park",
        "2026-05-06T02:00:00+00:00",
    )
    monkeypatch.setattr(main, "_events", lambda: [future])
    response = main.app.test_client().get("/")
    assert b"All clear until " in response.data
    assert b">tomorrow<" in response.data


def test_index_verb_default_from_env(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.setenv("VERB", "punished")
    response = main.app.test_client().get("/")
    assert b"Is my day punished?" in response.data


def test_index_verb_default_when_env_unset(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.delenv("VERB", raising=False)
    response = main.app.test_client().get("/")
    assert b"Is my day hosed?" in response.data


def test_index_url_verb_overrides_env(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.setenv("VERB", "punished")
    response = main.app.test_client().get("/fucked/")
    assert b"Is my day fucked?" in response.data
    assert b"punished" not in response.data


def test_index_has_viewport_meta(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/")
    assert b'name="viewport"' in response.data
    assert b"width=device-width" in response.data


def test_index_today_shows_quiet_until_line(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    # Event today, next quiet day is tomorrow (5/5).
    today_evt = _ev("Mets at Giants", "Oracle Park", "2026-05-05T02:05:00+00:00")
    monkeypatch.setattr(main, "_events", lambda: [today_evt])
    response = main.app.test_client().get("/")
    assert b"No peace and quiet until " in response.data
    assert b">tomorrow<" in response.data


def test_index_today_quiet_until_uses_weekday(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    # Today (Mon 5/4) + Tue 5/5 are busy; Wed 5/6 is quiet → "Wednesday".
    e1 = _ev("today", "Oracle Park", "2026-05-05T02:05:00+00:00")
    e2 = _ev("tomorrow", "Chase Center", "2026-05-06T02:00:00+00:00")
    monkeypatch.setattr(main, "_events", lambda: [e1, e2])
    response = main.app.test_client().get("/")
    assert b"No peace and quiet until " in response.data
    assert b">Wednesday<" in response.data


def test_index_specific_date_uses_that_date_as_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Don't pin "now" — the URL date should drive the answer regardless.
    target_event = _ev(
        "Mets at Giants",
        "Oracle Park",
        "2026-05-16T02:05:00+00:00",  # 2026-05-15 19:05 PT
    )
    monkeypatch.setattr(main, "_events", lambda: [target_event])
    response = main.app.test_client().get("/2026-05-15")
    assert response.status_code == 200
    assert b"8ball-yes-1.gif" in response.data
    assert b"halo-giants" in response.data


def test_index_specific_date_with_verb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/fucked/2026-05-15")
    assert response.status_code == 200
    assert b"Is my day fucked?" in response.data


def test_index_invalid_date_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    # Regex matches the shape but the date itself is invalid → 404.
    response = main.app.test_client().get("/2026-13-32")
    assert response.status_code == 404


def test_index_warriors_colorized_and_blue_verb(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    today_evt = _ev(
        "Los Angeles Lakers at Golden State Warriors",
        "Chase Center",
        "2026-05-05T03:00:00+00:00",
    )
    monkeypatch.setattr(main, "_events", lambda: [today_evt])
    response = main.app.test_client().get("/")
    assert b'class="warriors">Golden State Warriors</span>' in response.data
    assert b"halo-warriors" in response.data
    assert b"halo-giants" not in response.data
    assert b'class="verb warriors"' in response.data
