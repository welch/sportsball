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


def _ev(name: str, venue: str, when_utc: str, category: str = "sports") -> Event:
    return Event(
        source="test",
        source_id=name,
        name=name,
        starts_at=datetime.fromisoformat(when_utc),
        venue=venue,
        category=category,  # type: ignore[arg-type]
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


def test_index_shows_page_date_with_day_color(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    today_event = _ev("Mets at Giants", "Oracle Park", "2026-05-05T02:05:00+00:00")
    monkeypatch.setattr(main, "_events", lambda: [today_event])
    response = main.app.test_client().get("/")
    assert b'class="page-date giants"' in response.data
    assert b"Monday, May 4, 2026" in response.data


def test_index_page_date_neutral_when_no_color(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/")
    assert b'class="page-date"' in response.data
    # No color class appended when no events / mixed.
    assert b'class="page-date giants"' not in response.data
    assert b'class="page-date warriors"' not in response.data
    assert b'class="page-date concert"' not in response.data


def test_index_page_date_reflects_url_isodate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/2026-12-25")
    assert b"Friday, December 25, 2026" in response.data


def test_index_shows_last_updated_footer(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    import time as _time

    main._cache["events"] = []
    main._cache["fetched_at"] = _time.time()
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/")
    assert b"last updated " in response.data
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0


def test_index_footer_links_to_github_repo(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    import time as _time

    main._cache["events"] = []
    main._cache["fetched_at"] = _time.time()
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/")
    body = response.data
    # Anchor wrapping the footer text, opening in a new tab with safe rel.
    assert b'href="https://github.com/welch/sportsball"' in body
    assert b'target="_blank"' in body
    assert b'rel="noopener noreferrer"' in body
    # The anchor wraps the existing footer text, not a separate node.
    assert b'sportsball" target="_blank" rel="noopener noreferrer">last updated ' in body
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0


def test_footer_link_inherits_styling_in_served_css() -> None:
    response = main.app.test_client().get("/static/css/8ball.css")
    assert response.status_code == 200
    css = response.data.decode()
    # Anchor must inherit color and drop underline across all link states so
    # the footer looks identical whether visited or not.
    assert "footer a" in css
    assert "footer a:hover" in css
    assert "footer a:visited" in css
    assert "footer a:active" in css
    assert "color: inherit" in css
    assert "text-decoration: none" in css


def test_refresh_requires_cron_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/tasks/refresh")
    assert response.status_code == 403


def test_refresh_with_cron_header_invalidates_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def fake_events() -> list:
        calls["count"] += 1
        return []

    # Pre-warm cache with stale data so we can verify it's bypassed.
    main._cache["events"] = ["should be replaced"]
    main._cache["fetched_at"] = 9_999_999_999.0
    monkeypatch.setattr(main, "_events", fake_events)

    response = main.app.test_client().get("/tasks/refresh", headers={"X-Appengine-Cron": "true"})
    assert response.status_code == 200
    assert b"refreshed" in response.data
    assert calls["count"] == 1
    # Reset cache state for downstream tests.
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0


def test_static_urls_carry_cache_bust_version(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/")
    body = response.data.decode()
    # All static asset references must include the ?v=<hash> query.
    assert f"/static/css/8ball.css?v={main.STATIC_HASH}" in body
    assert f"/static/img/icon-48.png?v={main.STATIC_HASH}" in body
    assert f"/static/img/8ball-no-1.gif?v={main.STATIC_HASH}" in body
    # And the hash isn't trivially empty.
    assert len(main.STATIC_HASH) >= 8


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


def test_index_concert_today_uses_concert_halo_and_verb(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    concert = _ev(
        "Demi Lovato: It's Not That Deep Tour",
        "Chase Center",
        "2026-05-05T03:00:00+00:00",
        category="concert",
    )
    monkeypatch.setattr(main, "_events", lambda: [concert])
    response = main.app.test_client().get("/")
    assert b"halo-concert" in response.data
    assert b'class="verb concert"' in response.data
    # No team sports today → no team halos.
    assert b"halo-warriors" not in response.data
    assert b"halo-giants" not in response.data


def test_index_concert_at_oracle_park_uses_concert_halo(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    concert = _ev(
        "Fuerza Regida",
        "Oracle Park",
        "2026-05-05T03:00:00+00:00",
        category="concert",
    )
    monkeypatch.setattr(main, "_events", lambda: [concert])
    response = main.app.test_client().get("/")
    assert b"halo-concert" in response.data
    assert b'class="verb concert"' in response.data
    assert b"halo-giants" not in response.data


def test_index_valkyries_treated_as_sports(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    valks = _ev(
        "Phoenix Mercury at Golden State Valkyries",
        "Chase Center",
        "2026-05-05T03:00:00+00:00",
        category="sports",
    )
    monkeypatch.setattr(main, "_events", lambda: [valks])
    response = main.app.test_client().get("/")
    assert b"halo-warriors" in response.data
    assert b'class="verb warriors"' in response.data
    assert b'class="warriors">Golden State Valkyries</span>' in response.data
    assert b"halo-concert" not in response.data


def test_index_mixed_sports_and_concert_neutral_verb(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    game = _ev(
        "New York Mets at San Francisco Giants",
        "Oracle Park",
        "2026-05-05T02:05:00+00:00",
        category="sports",
    )
    show = _ev(
        "Some Concert",
        "Chase Center",
        "2026-05-05T03:00:00+00:00",
        category="concert",
    )
    monkeypatch.setattr(main, "_events", lambda: [game, show])
    response = main.app.test_client().get("/")
    # All three halos render (giants sports + concert).
    assert b"halo-giants" in response.data
    assert b"halo-concert" in response.data
    # Mixed kinds → no single verb color class.
    assert b'class="verb giants"' not in response.data
    assert b'class="verb warriors"' not in response.data
    assert b'class="verb concert"' not in response.data


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
