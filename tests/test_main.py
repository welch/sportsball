from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from sportsball import main, stats
from sportsball.aggregator import PT
from sportsball.models import Event

UTC = ZoneInfo("UTC")


@pytest.fixture(autouse=True)
def _reset_stats() -> None:
    stats.reset()


@pytest.fixture(autouse=True)
def _no_live_adapter_fetches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing in this suite should reach MLB/NBA/Ticketmaster over the wire.

    Both `_events()` (on a storage miss) and `/tasks/refresh` call
    `fetch_all(_adapters())` for real, so any test that reaches either path
    without stubbing it makes live HTTP calls — slow, and dependent on
    somebody else's uptime. Tests that care about the fetch result override
    this with their own `monkeypatch.setattr(main, "fetch_all", ...)`.
    """
    monkeypatch.setattr(main, "fetch_all", lambda adapters: [])


@pytest.fixture(autouse=True)
def _reset_events_cache() -> None:
    """`main._cache` is module-level, so one test's snapshot (or blob
    generation) would otherwise decide whether the next one reloads.
    """
    main._cache.update(
        events=None,
        fetched_at=0.0,
        previously_unseen=[],
        generation=None,
        checked_at=0.0,
    )


@pytest.fixture(autouse=True)
def _stub_cloud_logging_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests hitting `/health/<token>` would otherwise reach Cloud Logging
    via `stats.request_summary()`. Stub at the function level so the health
    page renders an empty summary by default.
    """
    monkeypatch.setattr(
        stats,
        "_query_request_summary",
        lambda window_hours=24: stats.RequestSummary(window_hours=window_hours),
    )


@pytest.fixture
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=PT)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
            return now if tz is None else now.astimezone(tz)  # type: ignore[arg-type]

    monkeypatch.setattr(main, "datetime", _Frozen)
    return now


def _ev(name: str, venue: str, when_utc: str, kind: str = "home") -> Event:
    return Event(
        source="test",
        source_id=name,
        name=name,
        starts_at=datetime.fromisoformat(when_utc),
        venue=venue,
        kind=kind,  # type: ignore[arg-type]
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


def test_index_verb_comes_from_the_requested_host(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """One deployment, two domains, one word of difference."""
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.setenv("HOST_VERBS", "example.com=punished, polite.example=bothered")
    client = main.app.test_client()
    assert b"Is my day punished?" in client.get("/", headers={"Host": "example.com"}).data
    assert b"Is my day bothered?" in client.get("/", headers={"Host": "polite.example"}).data


def test_index_verb_falls_back_to_the_primary_host(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """localhost isn't in the map and isn't redirected, so it has to borrow a
    verb — the primary host's, so local dev shows what the live site shows."""
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.setenv("HOST_VERBS", "example.com=punished, polite.example=bothered")
    response = main.app.test_client().get("/", headers={"Host": "localhost:5000"})
    assert b"Is my day punished?" in response.data


def test_index_verb_default_when_env_unset(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.delenv("HOST_VERBS", raising=False)
    response = main.app.test_client().get("/")
    assert b"Is my day hosed?" in response.data


def test_host_verbs_ignores_malformed_entries(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """A typo in one domain must not take the site down for the others."""
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.setenv("HOST_VERBS", " , example.com=punished ,noequals, =novrb, host=")
    with main.app.test_request_context("/", headers={"Host": "example.com"}):
        assert main._host_verbs() == {"example.com": "punished"}
    response = main.app.test_client().get("/", headers={"Host": "example.com"})
    assert b"Is my day punished?" in response.data


def test_host_verbs_lookup_ignores_case_and_port(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.setenv("HOST_VERBS", "Example.COM=punished")
    response = main.app.test_client().get("/", headers={"Host": "EXAMPLE.com:8080"})
    assert response.status_code == 200
    assert b"Is my day punished?" in response.data


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


def test_index_page_date_reflects_url_isodate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/2026-12-25")
    assert b"Friday, December 25, 2026" in response.data


def test_about_page_shows_the_refresh_time(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """The refresh time used to sit in every page's footer, where it read as
    noise and left the about link named after a timestamp. It belongs on the
    page that explains where the data comes from."""
    import time as _time

    main._cache["events"] = []
    main._cache["fetched_at"] = _time.time()
    body = main.app.test_client().get("/about").data.decode()
    assert 'class="updated"' in body
    assert "last landed" in body
    assert main._last_updated_label() in body
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0


def test_about_page_loads_the_snapshot_when_cache_is_cold(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """A visitor can land on /about first, so the page has to prime the cache
    itself or it has no refresh time to report."""
    loaded: list[bool] = []

    def fake_events() -> list:
        loaded.append(True)
        main._cache["events"] = []
        return []

    main._cache["events"] = None
    monkeypatch.setattr(main, "_events", fake_events)
    assert main.app.test_client().get("/about").status_code == 200
    assert loaded == [True]
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0


def test_index_footer_links_to_about_page(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/").data
    # Anchor target is the in-app /about page, not the GitHub repo.
    assert b'href="/about"' in body
    # Named for where it goes. It used to be named for the timestamp it
    # carried, which told a first-time visitor nothing about what was there.
    assert b'href="/about">about</a>' in body
    # External-link attributes from an earlier version are gone.
    assert b"github.com/welch/sportsball" not in body
    assert b'target="_blank"' not in body


def test_footer_survives_a_cold_cache(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> None:
    """The footer used to render only when there was a timestamp to show, so
    the site's one link to /about vanished on an instance that had never
    loaded a snapshot."""
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    monkeypatch.setattr(main, "_events", lambda: [])
    assert b'href="/about">about</a>' in main.app.test_client().get("/").data
    assert b'href="/about">about</a>' in main.app.test_client().get("/calendar/").data


def test_about_page_has_repo_link_and_back_link() -> None:
    response = main.app.test_client().get("/about")
    assert response.status_code == 200
    body = response.data
    assert b"github.com/welch/sportsball" in body
    # Back to today
    assert b'href="/"' in body


def test_about_page_renders_one_of_the_mlb_cities(monkeypatch: pytest.MonkeyPatch) -> None:
    """The "...but what about <city>?" header picks a random MLB city per
    request. Pin the choice so we can assert it lands in the heading.
    """
    monkeypatch.setattr(main.random, "choice", lambda seq: "Detroit")
    response = main.app.test_client().get("/about")
    assert b"but what about Detroit?" in response.data


def test_mlb_cities_excludes_home_city() -> None:
    """SF should not be in the rotation — the page is asking 'what about
    OTHER cities?'"""
    assert "San Francisco" not in main.MLB_CITIES


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


def test_refresh_fetches_writes_storage_and_updates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    e1 = _ev("Mets at SF Giants", "Oracle Park", "2026-05-15T19:00:00+00:00")
    e2 = _ev("Lakers at GS Warriors", "Chase Center", "2026-05-16T19:00:00+00:00")
    fetched_events = [e1, e2]
    fetch_calls = {"n": 0}

    def fake_fetch_all(adapters: list) -> list:
        fetch_calls["n"] += 1
        return fetched_events

    write_calls: list[tuple] = []

    def fake_write(events: list, fetched_at: object, **kwargs: object) -> None:
        write_calls.append((events, fetched_at, kwargs))

    # Previous cron's snapshot already had e1 — only e2 is new.
    monkeypatch.setattr(main, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(main.store, "write_events", fake_write)
    monkeypatch.setattr(main.store, "read_events", lambda: ([e1], object(), [], []))
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    main._cache["previously_unseen"] = []

    response = main.app.test_client().get("/tasks/refresh", headers={"X-Appengine-Cron": "true"})
    assert response.status_code == 200
    assert b"refreshed: 2 events (1 new)" in response.data
    assert fetch_calls["n"] == 1
    assert len(write_calls) == 1
    _events, _fetched_at, kwargs = write_calls[0]
    assert [e.source_id for e in kwargs["previously_unseen"]] == [e2.source_id]
    assert main._cache["events"] is fetched_events
    assert main._cache["fetched_at"] > 0.0
    assert [e.source_id for e in main._cache["previously_unseen"]] == [e2.source_id]
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    main._cache["previously_unseen"] = []


def test_refresh_first_run_marks_everything_new(monkeypatch: pytest.MonkeyPatch) -> None:
    e1 = _ev("event 1", "Oracle Park", "2026-05-15T19:00:00+00:00")
    monkeypatch.setattr(main, "fetch_all", lambda adapters: [e1])
    monkeypatch.setattr(main.store, "write_events", lambda *a, **k: None)
    # No prior snapshot exists.
    monkeypatch.setattr(main.store, "read_events", lambda: None)
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    main._cache["previously_unseen"] = []

    response = main.app.test_client().get("/tasks/refresh", headers={"X-Appengine-Cron": "true"})
    assert response.status_code == 200
    assert b"refreshed: 1 events (1 new)" in response.data
    assert [e.source_id for e in main._cache["previously_unseen"]] == [e1.source_id]
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    main._cache["previously_unseen"] = []


def test_refresh_preserves_last_success_across_failed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a single bad cron used to wipe out historical
    last_success_at. Pre-loading the prior snapshot before fetch_all
    keeps the success timestamp around for any adapter that fails now."""
    from datetime import datetime as _dt

    from sportsball.stats import AdapterStats

    last_success = _dt(2026, 5, 10, 6, 0, tzinfo=PT)
    prior_snapshot = [
        AdapterStats(
            name="warriors.fetch_events",
            last_success_at=last_success,
            last_event_count=89,
        )
    ]

    # Prior storage state has the success record. fetch_all now fails for
    # warriors via the resilience layer; we simulate that by having
    # fetch_all itself record the failure.
    def fake_fetch_all(adapters: list) -> list:
        stats.record_adapter_failure("warriors.fetch_events", "HTTPError: 403")
        return []

    monkeypatch.setattr(main, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(main.store, "write_events", lambda *a, **k: None)
    monkeypatch.setattr(main.store, "read_events", lambda: ([], object(), [], prior_snapshot))
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    main._cache["previously_unseen"] = []

    response = main.app.test_client().get("/tasks/refresh", headers={"X-Appengine-Cron": "true"})
    assert response.status_code == 200
    [snap] = stats.adapter_stats(["warriors.fetch_events"])
    assert snap.last_success_at == last_success  # history preserved
    assert snap.last_event_count == 89
    assert snap.last_error == "HTTPError: 403"
    assert snap.last_failure_at is not None
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    main._cache["previously_unseen"] = []


def test_refresh_continues_when_storage_write_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "fetch_all", lambda adapters: [])
    monkeypatch.setattr(main.store, "read_events", lambda: None)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("storage unreachable")

    monkeypatch.setattr(main.store, "write_events", boom)
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    main._cache["previously_unseen"] = []

    response = main.app.test_client().get("/tasks/refresh", headers={"X-Appengine-Cron": "true"})
    # Storage failure shouldn't break the cron — local cache still updated.
    assert response.status_code == 200
    assert main._cache["events"] == []
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    main._cache["previously_unseen"] = []


def test_events_reads_from_storage_on_cache_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime as _datetime

    stored_events: list = ["from-storage"]
    stored_at = _datetime(2026, 5, 6, 6, 0, tzinfo=PT)
    stored_new: list = ["just-arrived"]
    monkeypatch.setattr(
        main.store, "read_events", lambda: (stored_events, stored_at, stored_new, [])
    )
    monkeypatch.setattr(main, "fetch_all", lambda adapters: pytest.fail("should not fetch"))
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    main._cache["previously_unseen"] = []

    result = main._events()
    assert result is stored_events
    assert main._cache["fetched_at"] == stored_at.timestamp()
    assert main._cache["previously_unseen"] == stored_new
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    main._cache["previously_unseen"] = []


def test_events_falls_back_to_adapters_when_storage_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main.store, "read_events", lambda: None)
    monkeypatch.setattr(main, "fetch_all", lambda adapters: ["from-fetch"])
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    main._cache["previously_unseen"] = []

    result = main._events()
    assert result == ["from-fetch"]
    assert main._cache["previously_unseen"] == []
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    main._cache["previously_unseen"] = []


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


def test_index_specific_date_carries_the_host_verb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.setenv("HOST_VERBS", "example.com=punished")
    response = main.app.test_client().get("/2026-05-15", headers={"Host": "example.com"})
    assert response.status_code == 200
    assert b"Is my day punished?" in response.data


def test_verb_path_segments_are_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The URL verb was a mirroring-scheme leftover: it made every page
    reachable at unboundedly many URLs, which is what forced the canonical
    tags and the extra robots rule. The domain carries the verb now."""
    monkeypatch.setattr(main, "_events", lambda: [])
    client = main.app.test_client()
    assert client.get("/fucked/").status_code == 404
    assert client.get("/fucked/2026-05-15").status_code == 404
    assert client.get("/fucked/calendar/2026-05").status_code == 404


def test_index_invalid_date_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    # Regex matches the shape but the date itself is invalid → 404.
    response = main.app.test_client().get("/2026-13-32")
    assert response.status_code == 404


def test_index_non_team_event_at_chase_uses_dashed_chase_ring(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    concert = _ev(
        "Demi Lovato: It's Not That Deep Tour",
        "Chase Center",
        "2026-05-05T03:00:00+00:00",
        kind="event",
    )
    monkeypatch.setattr(main, "_events", lambda: [concert])
    response = main.app.test_client().get("/")
    assert b"ring-chase" in response.data
    # Hue still says Chase Center even though no home team is playing.
    assert b'class="verb warriors"' in response.data
    # No home game today → no solid glow anywhere.
    assert b"halo-warriors" not in response.data
    assert b"halo-giants" not in response.data


def test_index_non_team_event_at_oracle_uses_dashed_oracle_ring(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    concert = _ev(
        "Fuerza Regida",
        "Oracle Park",
        "2026-05-05T03:00:00+00:00",
        kind="event",
    )
    monkeypatch.setattr(main, "_events", lambda: [concert])
    response = main.app.test_client().get("/")
    assert b"ring-oracle" in response.data
    assert b'class="verb giants"' in response.data
    assert b"halo-giants" not in response.data
    assert b"ring-chase" not in response.data


def test_index_monster_trucks_do_not_wear_the_warriors_glow(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """Ticketmaster files monster trucks under segment "Sports", which is how
    they used to come out looking exactly like a Warriors home game."""
    trucks = _ev(
        "HOT WHEELS MONSTER TRUCKS LIVE  GLOW-N-FIRE",
        "Chase Center",
        "2026-05-05T03:00:00+00:00",
        kind="event",
    )
    monkeypatch.setattr(main, "_events", lambda: [trucks])
    response = main.app.test_client().get("/")
    assert b"ring-chase" in response.data
    assert b"halo-warriors" not in response.data


def test_index_valkyries_count_as_a_home_team(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    valks = _ev(
        "Phoenix Mercury at Golden State Valkyries",
        "Chase Center",
        "2026-05-05T03:00:00+00:00",
        kind="home",
    )
    monkeypatch.setattr(main, "_events", lambda: [valks])
    response = main.app.test_client().get("/")
    assert b"halo-warriors" in response.data
    assert b'class="verb warriors"' in response.data
    assert b'class="warriors">Golden State Valkyries</span>' in response.data
    assert b"ring-chase" not in response.data


def test_index_home_game_and_other_event_at_same_venue_show_both_marks(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """The two channels compose: a glow and a dash in the same hue."""
    game = _ev(
        "New York Mets at San Francisco Giants",
        "Oracle Park",
        "2026-05-05T02:05:00+00:00",
        kind="home",
    )
    show = _ev(
        "Noah Kahan",
        "Oracle Park",
        "2026-05-05T03:00:00+00:00",
        kind="event",
    )
    monkeypatch.setattr(main, "_events", lambda: [game, show])
    response = main.app.test_client().get("/")
    assert b"halo-giants" in response.data
    assert b"ring-oracle" in response.data
    # One venue owns the day, so the verb still takes its color.
    assert b'class="verb giants"' in response.data


def test_index_both_venues_active_gives_neutral_verb(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    game = _ev(
        "New York Mets at San Francisco Giants",
        "Oracle Park",
        "2026-05-05T02:05:00+00:00",
        kind="home",
    )
    show = _ev(
        "Some Concert",
        "Chase Center",
        "2026-05-05T03:00:00+00:00",
        kind="event",
    )
    monkeypatch.setattr(main, "_events", lambda: [game, show])
    response = main.app.test_client().get("/")
    assert b"halo-giants" in response.data
    assert b"ring-chase" in response.data
    # Two venues → no single verb color.
    assert b'class="verb giants"' not in response.data
    assert b'class="verb warriors"' not in response.data


def test_no_redirect_for_any_mapped_host(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """Every domain in the map serves itself. Redirecting the secondary to
    the primary would defeat the point of having a second domain."""
    monkeypatch.setenv("HOST_VERBS", "example.com=punished, polite.example=bothered")
    monkeypatch.setattr(main, "_events", lambda: [])
    client = main.app.test_client()
    assert client.get("/", headers={"Host": "example.com"}).status_code == 200
    assert client.get("/", headers={"Host": "polite.example"}).status_code == 200


def test_unmapped_host_redirects_to_the_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_VERBS", "example.com=punished, polite.example=bothered")
    response = main.app.test_client().get("/", headers={"Host": "sports-ball.appspot.com"})
    assert response.status_code == 301
    # Werkzeug normalizes a bare trailing "?" off the Location URL.
    assert response.headers["Location"].rstrip("?") == "https://example.com/"


def test_www_of_a_mapped_host_redirects_to_that_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`www.` belongs to its own domain, not to the primary one. Folding it
    onto the first entry would bounce a visitor who deliberately typed the
    polite name across to the profane one."""
    monkeypatch.setenv("HOST_VERBS", "example.com=punished, polite.example=bothered")
    response = main.app.test_client().get("/", headers={"Host": "www.polite.example"})
    assert response.status_code == 301
    assert response.headers["Location"].rstrip("?") == "https://polite.example/"


def test_www_of_a_mapped_host_preserves_the_path_and_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_VERBS", "example.com=punished, polite.example=bothered")
    response = main.app.test_client().get(
        "/2026-08-21?nav=chevron", headers={"Host": "www.polite.example"}
    )
    assert response.status_code == 301
    assert response.headers["Location"] == "https://polite.example/2026-08-21?nav=chevron"


def test_www_of_the_primary_host_still_reaches_the_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The primary is the first entry, so this held before www was understood
    too — pinned so stripping `www.` can't regress it."""
    monkeypatch.setenv("HOST_VERBS", "example.com=punished, polite.example=bothered")
    response = main.app.test_client().get("/", headers={"Host": "www.example.com"})
    assert response.status_code == 301
    assert response.headers["Location"].rstrip("?") == "https://example.com/"


def test_www_of_an_unmapped_host_still_folds_onto_the_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stripping `www.` only helps when what's left is a domain we serve.
    A stale alias stays consolidated on the primary."""
    monkeypatch.setenv("HOST_VERBS", "example.com=punished, polite.example=bothered")
    response = main.app.test_client().get("/", headers={"Host": "www.stale.example"})
    assert response.status_code == 301
    assert response.headers["Location"].rstrip("?") == "https://example.com/"


def test_unmapped_host_redirect_preserves_query_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_VERBS", "example.com=punished")
    response = main.app.test_client().get(
        "/some/path?foo=bar", headers={"Host": "sports-ball.appspot.com"}
    )
    assert response.status_code == 301
    assert response.headers["Location"].endswith("?foo=bar")
    assert response.headers["Location"] == "https://example.com/some/path?foo=bar"


def test_unmapped_host_does_not_redirect_healthz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_VERBS", "example.com=punished")
    response = main.app.test_client().get("/healthz", headers={"Host": "sports-ball.appspot.com"})
    assert response.status_code == 200
    assert response.data == b"ok"


REPO = "https://github.com/example/sportsball"


@pytest.fixture
def repo_url(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("REPO_URL", REPO)
    return REPO


def test_version_info_unset_env(repo_url: str) -> None:
    version = main._version_info()
    assert "local" in version.label.lower()
    assert version.url == REPO


def test_version_info_pretty_prints_clean_bin_deploy_id(
    monkeypatch: pytest.MonkeyPatch, repo_url: str
) -> None:
    """Clean trees → no `-clean` suffix; just <tag>-<sha>."""
    monkeypatch.setenv("GAE_VERSION", "v0-4-0-dc9473a")
    assert main._version_info() == ("v0.4.0+dc9473a", f"{REPO}/commit/dc9473a")


def test_version_info_marks_dirty(monkeypatch: pytest.MonkeyPatch, repo_url: str) -> None:
    """The SHA still links its commit — "(dirty)" is what says the running
    code isn't exactly that tree."""
    monkeypatch.setenv("GAE_VERSION", "v0-4-0-dc9473a-dirty")
    assert main._version_info() == ("v0.4.0+dc9473a (dirty)", f"{REPO}/commit/dc9473a")


def test_version_info_falls_back_on_timestamp_id(
    monkeypatch: pytest.MonkeyPatch, repo_url: str
) -> None:
    """Bare `gcloud app deploy` (no bin/deploy) gives a timestamp-shaped ID
    that can't be parsed; show it raw so the operator notices, and link the
    repo root since there's no commit to point at."""
    monkeypatch.setenv("GAE_VERSION", "20260507t170000")
    assert main._version_info() == ("20260507t170000", REPO)


def test_version_info_falls_back_on_unparseable(
    monkeypatch: pytest.MonkeyPatch, repo_url: str
) -> None:
    """Anything that doesn't end in a short-SHA-shaped trailing part shows raw."""
    monkeypatch.setenv("GAE_VERSION", "manual-deploy")
    assert main._version_info() == ("manual-deploy", REPO)


def test_version_info_tolerates_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    """A copy-pasted repo URL often carries a trailing slash; don't emit `//commit`."""
    monkeypatch.setenv("REPO_URL", f"{REPO}/")
    monkeypatch.setenv("GAE_VERSION", "v0-4-0-dc9473a")
    assert main._version_info().url == f"{REPO}/commit/dc9473a"


def test_version_info_unlinked_when_repo_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fork that hasn't configured REPO_URL shows the build string plain
    rather than linking the operator into someone else's source."""
    monkeypatch.delenv("REPO_URL", raising=False)
    monkeypatch.setenv("GAE_VERSION", "v0-4-0-dc9473a")
    assert main._version_info() == ("v0.4.0+dc9473a", None)


def test_health_loads_adapter_stats_from_storage_on_fresh_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug fix: a fresh instance whose first request is /health used to show
    every adapter as "never" because /health read _cache directly without
    triggering the storage load. Verify the storage adapter_stats land
    on the page even when the cache starts empty.
    """
    from datetime import datetime as _dt

    from sportsball.stats import AdapterStats

    monkeypatch.setenv("HEALTH_TOKEN", "secret")
    fetched_at = _dt(2026, 5, 8, 6, 0, tzinfo=PT)
    snapshot = [
        AdapterStats(
            name="giants.fetch_events",
            last_success_at=fetched_at,
            last_event_count=42,
        )
    ]
    monkeypatch.setattr(main.store, "read_events", lambda: ([], fetched_at, [], snapshot))
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    main._cache["previously_unseen"] = []

    response = main.app.test_client().get("/health/secret")
    assert response.status_code == 200
    body = response.data.decode()
    assert "giants.fetch_events" in body
    assert "42" in body
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0
    main._cache["previously_unseen"] = []


def test_health_page_renders_version_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEALTH_TOKEN", "secret")
    monkeypatch.setenv("GAE_VERSION", "v0-4-0-dc9473a")
    response = main.app.test_client().get("/health/secret")
    assert response.status_code == 200
    assert b"v0.4.0+dc9473a" in response.data


def test_health_page_links_version_to_its_commit(
    monkeypatch: pytest.MonkeyPatch, repo_url: str
) -> None:
    monkeypatch.setenv("HEALTH_TOKEN", "secret")
    monkeypatch.setenv("GAE_VERSION", "v0-4-0-dc9473a")
    response = main.app.test_client().get("/health/secret")
    assert f'href="{REPO}/commit/dc9473a"' in response.data.decode()


def test_health_page_renders_version_unlinked_without_repo_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REPO_URL", raising=False)
    monkeypatch.setenv("HEALTH_TOKEN", "secret")
    monkeypatch.setenv("GAE_VERSION", "v0-4-0-dc9473a")
    body = main.app.test_client().get("/health/secret").data.decode()
    assert "<code>v0.4.0+dc9473a</code>" in body
    assert "/commit/dc9473a" not in body


def test_health_page_links_timestamp_to_that_days_page(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """The "as of" stamp is a way back to what the site was saying that day."""
    monkeypatch.setenv("HEALTH_TOKEN", "secret")
    response = main.app.test_client().get("/health/secret")
    assert 'href="/2026-05-04"' in response.data.decode()


def test_unmapped_host_does_not_redirect_localhost(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """Dev convenience: localhost requests skip the redirect even when
    HOST_VERBS is set, so a dev whose local env.yaml carries the prod map
    can still hit http://localhost:PORT without bouncing.
    """
    monkeypatch.setenv("HOST_VERBS", "example.com=punished")
    monkeypatch.setattr(main, "_events", lambda: [])
    for host in ("localhost:5071", "127.0.0.1:5071", "localhost", "0.0.0.0:8080"):
        response = main.app.test_client().get("/", headers={"Host": host})
        assert response.status_code == 200, f"unexpected redirect for Host: {host}"


def test_unmapped_host_does_not_redirect_cron(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_VERBS", "example.com=punished")
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get(
        "/tasks/refresh",
        headers={"Host": "sports-ball.appspot.com", "X-Appengine-Cron": "true"},
    )
    assert response.status_code == 200
    # Reset cache state for downstream tests.
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0


def test_host_verbs_unset_never_redirects(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.delenv("HOST_VERBS", raising=False)
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/", headers={"Host": "localhost:5000"})
    assert response.status_code == 200


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


# ---------------------------------------------------------------------------
# /health/<token>
# ---------------------------------------------------------------------------


def test_health_wrong_token_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEALTH_TOKEN", "right-token")
    response = main.app.test_client().get("/health/wrong-token")
    assert response.status_code == 404


def test_health_unset_token_env_404(monkeypatch: pytest.MonkeyPatch) -> None:
    # If the env var isn't set we shouldn't authorize anyone — including the
    # empty string — and we should never give a 200.
    monkeypatch.delenv("HEALTH_TOKEN", raising=False)
    response = main.app.test_client().get("/health/anything")
    assert response.status_code == 404


def test_health_right_token_renders_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEALTH_TOKEN", "secret")
    stats.record_adapter_success("giants.fetch_events", 5)
    stats.record_adapter_failure("warriors.fetch_events", "RuntimeError: boom")
    main._cache["events"] = []
    main._cache["fetched_at"] = 0.0
    try:
        response = main.app.test_client().get("/health/secret")
    finally:
        main._cache["events"] = None
        main._cache["fetched_at"] = 0.0
    assert response.status_code == 200
    body = response.data.decode()
    # All four adapters listed by canonical name.
    assert "giants.fetch_events" in body
    assert "warriors.fetch_events" in body
    assert "ticketmaster.fetch_oracle_park_events" in body
    assert "ticketmaster.fetch_chase_center_events" in body
    # Recorded telemetry surfaces in the page.
    import re

    assert re.search(r"<td[^>]*>\s*5\s*</td>", body)  # giants event count cell
    assert "RuntimeError: boom" in body
    # Section headings + cache counters present.
    assert "Adapters" in body
    assert "HTTP requests" in body
    assert "Event cache" in body


def test_health_records_per_adapter_via_fetch_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: a fetch_all run records per-adapter outcomes."""
    from sportsball.aggregator import fetch_all

    def good() -> list[Event]:
        return [
            Event(
                source="t",
                source_id="1",
                name="Mets at Giants",
                starts_at=datetime.fromisoformat("2026-05-05T02:05:00+00:00"),
                venue="Oracle Park",
            )
        ]

    def bad() -> list[Event]:
        raise RuntimeError("nope")

    fetch_all([("giants.fetch_events", good), ("warriors.fetch_events", bad)])
    snaps = {s.name: s for s in stats.adapter_stats(list(main.ADAPTER_NAMES))}
    assert snaps["giants.fetch_events"].last_event_count == 1
    assert snaps["giants.fetch_events"].last_error is None
    assert snaps["warriors.fetch_events"].last_error is not None
    assert "nope" in (snaps["warriors.fetch_events"].last_error or "")


def test_humanize_age_units() -> None:
    assert main._humanize_age(0) == "0 seconds ago"
    assert main._humanize_age(1) == "1 second ago"
    assert main._humanize_age(45) == "45 seconds ago"
    assert main._humanize_age(60) == "1 minute ago"
    assert main._humanize_age(180) == "3 minutes ago"
    # 1.2h → "1.2 hours ago"
    assert main._humanize_age(3600 * 1.2) == "1.2 hours ago"
    # exactly 1 day → "1.0 day ago"
    assert main._humanize_age(86400) == "1.0 day ago"
    assert main._humanize_age(86400 * 3.5) == "3.5 days ago"


def test_index_date_links_to_that_month_calendar(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/")
    assert b'href="/calendar/2026-05"' in response.data


def test_index_date_link_for_explicit_date_uses_that_month(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/2026-12-25")
    assert b'href="/calendar/2026-12"' in response.data


def test_index_date_link_is_a_bare_path(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """Links stay host-relative, so a visitor never leaves the domain they
    arrived on — and the verb follows for free."""
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.setenv("HOST_VERBS", "example.com=punished, polite.example=bothered")
    response = main.app.test_client().get("/", headers={"Host": "polite.example"})
    assert b'href="/calendar/2026-05"' in response.data
    assert b"example.com" not in response.data


def test_calendar_renders_month_grid(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/calendar/2026-05")
    assert response.status_code == 200
    body = response.data
    assert b"May 2026" in body
    assert b"Sun" in body and b"Sat" in body
    # Every day of May is present as a link to its 8-ball view.
    assert b'href="/2026-05-01"' in body
    assert b'href="/2026-05-31"' in body


def test_calendar_bare_url_defaults_to_current_month(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/calendar/")
    assert response.status_code == 200
    assert b"May 2026" in response.data


def test_calendar_chevrons_step_a_month(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/calendar/2026-05").data
    assert b'href="/calendar/2026-04"' in body
    assert b'href="/calendar/2026-06"' in body


def test_calendar_chevrons_wrap_across_the_year(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/calendar/2026-01").data
    assert b'href="/calendar/2025-12"' in body
    assert b'href="/calendar/2026-02"' in body


def test_calendar_carries_the_host_verb(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.setenv("HOST_VERBS", "example.com=punished, polite.example=bothered")
    body = main.app.test_client().get("/calendar/2026-05", headers={"Host": "polite.example"}).data
    assert b"Is my month bothered?" in body
    assert b'href="/calendar/2026-04"' in body
    assert b'href="/2026-05-04"' in body


def test_calendar_day_wears_event_halos(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    giants = _ev("Mets at Giants", "Oracle Park", "2026-05-05T02:05:00+00:00")
    concert = _ev("Some Band", "Chase Center", "2026-05-05T03:00:00+00:00", "event")
    monkeypatch.setattr(main, "_events", lambda: [giants, concert])
    body = main.app.test_client().get("/calendar/2026-05").data.decode()
    # Both events land on 5/4 PT → that one cell carries both marks: a solid
    # orange glow for the Giants, a dashed blue ring for the Chase show.
    assert 'href="/2026-05-04"' in body
    assert "cal-day halo-giants ring-chase" in body


def test_calendar_marks_today(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/calendar/2026-05").data.decode()
    assert "is-today" in body
    # Only one cell is today.
    assert body.count("is-today") == 1


def test_calendar_other_month_has_no_today_marker(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/calendar/2026-09").data.decode()
    assert "is-today" not in body


def test_calendar_dims_adjacent_month_days(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/calendar/2026-05").data.decode()
    # May 2026 opens on Fri 5/1, so the grid spills back to Sun 4/26.
    assert 'href="/2026-04-26"' in body
    assert "outside" in body


def test_calendar_rejects_malformed_month(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    client = main.app.test_client()
    assert client.get("/calendar/2026-13").status_code == 404
    assert client.get("/calendar/not-a-month").status_code == 404


def test_calendar_links_home(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/calendar/2026-05").data
    assert b'href="/"' in body


def test_isodate_route_still_wins_over_month_route(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """`/2026-05-15` is a day, not a month — the converters must not collide."""
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/2026-05-15")
    assert response.status_code == 200
    assert b"Friday, May 15, 2026" in response.data


def _css_block(css: str, selector: str, after: int = 0) -> str:
    """The body of the rule whose selector list is exactly `selector`.

    Anchored on a newline so a lone `.ring-chase::after` doesn't match the
    combined `.ring-oracle::before,\\n.ring-chase::after` rule above it.
    """
    start = css.index("\n" + selector + " {", after)
    return css[start : css.index("}", start)]


def test_calendar_nav_shares_the_day_grid_geometry() -> None:
    """Chevrons must sit above the Sun/Sat columns so the pointer can stay
    put while paging through months. That only holds if the nav and the day
    grid have identical column geometry — a flex row would let a long month
    name like "September" shove them outward.
    """
    css = main.app.test_client().get("/static/css/8ball.css").data.decode()
    nav, grid = _css_block(css, ".cal-nav"), _css_block(css, ".cal-grid")
    for prop in ("grid-template-columns: repeat(7, 1fr)", "gap: 2px"):
        assert prop in nav, f"{prop} missing from .cal-nav"
        assert prop in grid, f"{prop} missing from .cal-grid"
    # The chevrons are pinned to the outer columns, and the title is boxed
    # into the middle five so it can never push them around.
    assert "grid-column: 1" in _css_block(css, ".chev.prev")
    assert "grid-column: 7" in _css_block(css, ".chev.next")
    assert "grid-column: 2 / 7" in _css_block(css, ".cal-title")


def test_calendar_chevrons_carry_position_classes(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/calendar/2026-05").data
    assert b'class="chev prev"' in body
    assert b'class="chev next"' in body


def test_venue_colorize_tints_tracked_venues() -> None:
    assert main.venue_colorize("Oracle Park") == '<span class="giants">Oracle Park</span>'
    assert main.venue_colorize("Chase Center") == '<span class="warriors">Chase Center</span>'


def test_venue_colorize_leaves_unknown_venues_plain() -> None:
    """An away game that slipped the venue filter shouldn't be guessed at."""
    assert main.venue_colorize("Dodger Stadium") == "Dodger Stadium"


def test_venue_colorize_escapes() -> None:
    assert main.venue_colorize("<script>") == "&lt;script&gt;"


def test_index_colors_venue_in_today_event_list(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    giants = _ev("Mets at Giants", "Oracle Park", "2026-05-05T02:05:00+00:00")
    show = _ev("Some Band", "Chase Center", "2026-05-05T03:00:00+00:00", kind="event")
    monkeypatch.setattr(main, "_events", lambda: [giants, show])
    body = main.app.test_client().get("/").data
    assert b'at <span class="giants">Oracle Park</span>' in body
    assert b'at <span class="warriors">Chase Center</span>' in body


def test_index_colors_venue_in_future_event_list(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    future = _ev("Warriors vs Lakers", "Chase Center", "2026-05-09T02:00:00+00:00")
    monkeypatch.setattr(main, "_events", lambda: [future])
    body = main.app.test_client().get("/").data
    assert b"All clear until" in body
    assert b'at <span class="warriors">Chase Center</span>' in body


def test_venue_rings_share_a_band_and_interleave() -> None:
    """The two dashed rings occupy one circle and must stay out of phase.

    Oracle draws across the first half of each period, Chase starts half a
    period later and lands in the gaps, so a day with events at both venues
    composites to a complete alternating ring. Two things break that: a duty
    cycle over 50% (the rings overlap) or a phase offset that isn't half the
    period (they collide or leave a seam).
    """
    css = main.app.test_client().get("/static/css/8ball.css").data.decode()
    shared = _css_block(css, ".ring-oracle::before,\n.ring-chase::after")
    # One band for both — sharing the radius is the whole point.
    assert shared.count("closest-side, transparent 78%, #000 90%, transparent 100%") == 2
    # Dashes end by the halfway mark, leaving the back half for the other ring.
    assert "transparent calc(var(--dash-period) * 0.5)" in shared
    for stop in ("0.11", "0.39"):
        assert f"calc(var(--dash-period) * {stop})" in shared

    # The lone `.ring-chase::after` rule lives after the combined one, whose
    # selector list ends with the same string.
    past_shared = css.index(shared) + len(shared)
    oracle = _css_block(css, ".ring-oracle::before")
    chase = _css_block(css, ".ring-chase::after", after=past_shared)
    assert "repeating-conic-gradient(from 0deg" in oracle
    # Derived from the period, so overriding the period can't desync them.
    assert "repeating-conic-gradient(from calc(var(--dash-period) / 2)" in chase


def test_ball_overrides_only_the_dash_period() -> None:
    """The ball needs a shorter period for its much longer circumference —
    but it must not restate the phase, or the two would drift apart."""
    css = main.app.test_client().get("/static/css/8ball.css").data.decode()
    block = _css_block(css, ".ball-frame.ring-oracle::before,\n.ball-frame.ring-chase::after")
    assert "--dash-period" in block
    assert "conic-gradient" not in block


def _warm_cache(events: list, *, generation: int | None, age: float = 0.0) -> None:
    """Put the cache in the state a warm instance is in: events loaded from
    a known blob generation, well inside the 12-hour TTL."""
    import time as _time

    main._cache.update(
        events=events,
        fetched_at=_time.time() - age,
        previously_unseen=[],
        generation=generation,
        checked_at=0.0,
    )


def test_events_reloads_when_blob_generation_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A warm instance inside its TTL still picks up a `bin/refresh` write,
    which is the whole point of the generation poll."""
    from datetime import datetime as _datetime

    _warm_cache(["stale"], generation=100)
    fresh = ["fresh"]
    monkeypatch.setattr(main.store, "current_generation", lambda: 101)
    monkeypatch.setattr(
        main.store,
        "read_events",
        lambda: (fresh, _datetime(2026, 5, 6, 6, 0, tzinfo=PT), [], []),
    )
    monkeypatch.setattr(main, "fetch_all", lambda adapters: pytest.fail("should not fetch"))

    assert main._events() is fresh
    assert main._cache["generation"] == 101


def test_events_serves_cache_when_generation_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    cached = ["cached"]
    _warm_cache(cached, generation=100)
    monkeypatch.setattr(main.store, "current_generation", lambda: 100)
    monkeypatch.setattr(main.store, "read_events", lambda: pytest.fail("should not re-download"))
    monkeypatch.setattr(main, "fetch_all", lambda adapters: pytest.fail("should not fetch"))

    assert main._events() is cached


def test_generation_poll_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """One metadata call per SNAPSHOT_POLL_SECONDS, not one per request."""
    import time as _time

    calls = []
    monkeypatch.setattr(main.store, "current_generation", lambda: calls.append(1) or 100)
    monkeypatch.setattr(main.store, "read_events", lambda: pytest.fail("should not re-download"))
    _warm_cache(["cached"], generation=100)

    for _ in range(5):
        main._events()
    assert len(calls) == 1

    # Push the last check back past the interval and it asks again.
    main._cache["checked_at"] = _time.time() - main.SNAPSHOT_POLL_SECONDS - 1
    main._events()
    assert len(calls) == 2


def test_events_keeps_cache_when_generation_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GCS hiccup (or no bucket at all) must not trigger a reload — an
    unknown generation means "can't tell", not "changed"."""
    cached = ["cached"]
    _warm_cache(cached, generation=100)
    monkeypatch.setattr(main.store, "current_generation", lambda: None)
    monkeypatch.setattr(main.store, "read_events", lambda: pytest.fail("should not re-download"))
    monkeypatch.setattr(main, "fetch_all", lambda adapters: pytest.fail("should not fetch"))

    assert main._events() is cached


def test_refresh_records_the_generation_it_wrote(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise the cron's own write reads back as somebody else's change
    and the instance re-downloads the payload it just built."""
    monkeypatch.setattr(main.store, "read_events", lambda: None)
    monkeypatch.setattr(main.store, "write_events", lambda *a, **k: None)
    monkeypatch.setattr(main.store, "current_generation", lambda: 202)
    monkeypatch.setattr(main, "fetch_all", lambda adapters: [])

    response = main.app.test_client().get("/tasks/refresh", headers={"X-Appengine-Cron": "true"})
    assert response.status_code == 200
    assert main._cache["generation"] == 202


# --- Browse-range bounds (the GPTBot crawler trap) -------------------------
#
# The calendar's chevrons used to step a month at a time forever, and every
# day cell linked to a day view that linked back to a calendar month. That's
# an unbounded URL space, and in Aug 2026 GPTBot walked it out to year 9241
# at ~7k requests/hour. The window below is far wider than any adapter's
# horizon, so humans never hit it; crawlers stop at the edge.


def test_browse_range_spans_a_year_either_side_of_today(fixed_now: datetime) -> None:
    earliest, latest = main._browse_range()
    assert earliest == date(2025, 1, 1)
    assert latest == date(2027, 12, 31)


def test_day_view_404s_outside_the_browse_range(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    client = main.app.test_client()
    assert client.get("/9241-09-03").status_code == 404
    assert client.get("/2024-12-31").status_code == 404
    assert client.get("/2028-01-01").status_code == 404


def test_day_view_serves_the_edges_of_the_browse_range(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    client = main.app.test_client()
    assert client.get("/2025-01-01").status_code == 200
    assert client.get("/2027-12-31").status_code == 200


def test_calendar_404s_outside_the_browse_range(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    client = main.app.test_client()
    assert client.get("/calendar/9241-09").status_code == 404
    assert client.get("/calendar/2024-12").status_code == 404
    assert client.get("/calendar/2028-01").status_code == 404


def test_calendar_chevron_is_dead_at_the_range_edge(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """The 404 alone doesn't stop a crawler — it follows links. At the edge
    the chevron must not be a link at all."""
    monkeypatch.setattr(main, "_events", lambda: [])
    client = main.app.test_client()

    first = client.get("/calendar/2025-01").data.decode()
    assert 'href="/calendar/2024-12"' not in first
    assert 'href="/calendar/2025-02"' in first  # forward still works

    last = client.get("/calendar/2027-12").data.decode()
    assert 'href="/calendar/2028-01"' not in last
    assert 'href="/calendar/2027-11"' in last


def test_calendar_edge_month_day_cells_stay_inside_the_range(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """The grid spills into adjacent months to fill whole weeks. On the
    first month those spill days fall outside the range, so they must not
    be links either."""
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/calendar/2025-01").data.decode()
    assert 'href="/2024-12-29"' not in body


def test_robots_txt_is_served_and_closes_the_operator_paths() -> None:
    response = main.app.test_client().get("/robots.txt")
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    body = response.data.decode()
    assert "Disallow: /health/" in body
    assert "Disallow: /tasks/" in body


def test_health_marks_a_truncated_scan_as_a_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bounded scan under-counts by design. The page has to say so, or the
    operator reads a flood as a quiet day."""
    monkeypatch.setenv("HEALTH_TOKEN", "t0ken")
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.setattr(
        stats,
        "_query_request_summary",
        lambda window_hours=24: stats.RequestSummary(
            total=3000, by_class={"2xx": 2900}, user_traffic=2900, truncated=True
        ),
    )
    body = main.app.test_client().get("/health/t0ken").data.decode()
    assert "3000+" in body
    assert "2900+" in body
    assert "lower bounds" in body


def test_health_does_not_mark_a_complete_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEALTH_TOKEN", "t0ken")
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.setattr(
        stats,
        "_query_request_summary",
        lambda window_hours=24: stats.RequestSummary(
            total=42, by_class={"2xx": 40}, user_traffic=40
        ),
    )
    body = main.app.test_client().get("/health/t0ken").data.decode()
    assert "42+" not in body
    assert "lower bounds" not in body


# --- Canonical URLs -------------------------------------------------------
#
# Each domain in HOST_VERBS self-canonicalizes. Folding them all onto the
# primary would consolidate the search signal, but it would also mean a
# searcher who found the polite domain got pointed at the impolite one —
# which is the one thing the polite domain exists to prevent. Everything
# else (appspot, an IP, a stale alias) names the primary instead of itself.


def test_day_view_canonical_points_at_itself(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/2026-05-15").data.decode()
    assert '<link rel="canonical" href="http://localhost/2026-05-15">' in body


def test_index_canonical_is_the_root(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/").data.decode()
    assert '<link rel="canonical" href="http://localhost/">' in body


def test_each_mapped_host_canonicalizes_to_itself(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setenv("HOST_VERBS", "example.com=punished, polite.example=bothered")
    monkeypatch.setattr(main, "_events", lambda: [])
    client = main.app.test_client()
    for host in ("example.com", "polite.example"):
        for path in ("/2026-05-15", "/calendar/2026-05"):
            body = client.get(path, headers={"Host": host}).data.decode()
            assert f'<link rel="canonical" href="https://{host}{path}">' in body


def test_unmapped_host_canonicalizes_to_the_primary(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """A page served on an unmapped host — a localhost preview, a health
    check — still names the real site rather than itself."""
    monkeypatch.setenv("HOST_VERBS", "example.com=punished, polite.example=bothered")
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/2026-05-15", headers={"Host": "localhost"}).data.decode()
    assert '<link rel="canonical" href="https://example.com/2026-05-15">' in body


# --- Navigation affordances -----------------------------------------------
#
# Which links are navigation is a template decision (`.nav-link`); how they
# look is the stylesheet's. These pin the machinery in between — the body
# class — and deliberately assert nothing about the visual, so the styles
# can be reworked in CSS alone.


def test_nav_hints_off_by_default(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/").data.decode()
    assert "<body>" in body
    assert "nav-hints" not in body


def test_nav_hints_are_per_host(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> None:
    """The point of the setting: the domain a stranger is handed can dress
    its links while the everyday domain stays undressed."""
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.setenv("HOST_VERBS", "example.com=punished, polite.example=bothered")
    monkeypatch.setenv("NAV_HINTS", "polite.example=chevron")
    client = main.app.test_client()
    plain = client.get("/", headers={"Host": "example.com"}).data.decode()
    hinted = client.get("/", headers={"Host": "polite.example"}).data.decode()
    assert "nav-hints" not in plain
    assert '<body class="nav-hints nav-chevron">' in hinted


def test_nav_hints_reach_the_calendar_too(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.setenv("NAV_HINTS", "polite.example=chevron")
    body = (
        main.app.test_client()
        .get("/calendar/2026-05", headers={"Host": "polite.example"})
        .data.decode()
    )
    assert '<body class="nav-hints nav-chevron">' in body


def test_nav_query_overrides_the_host_setting(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """`?nav=` is how you compare styles on one page without a restart."""
    monkeypatch.setattr(main, "_events", lambda: [])
    monkeypatch.delenv("NAV_HINTS", raising=False)
    client = main.app.test_client()
    hosted = {"Host": "polite.example"}
    assert (
        '<body class="nav-hints nav-chevron">'
        in client.get("/?nav=chevron", headers=hosted).data.decode()
    )
    # Anything the CSS can't draw — `off`, a retired style, a typo, an
    # injection attempt — falls back to plain rather than reaching the markup.
    monkeypatch.setenv("NAV_HINTS", "polite.example=chevron")
    for query in ("?nav=off", "?nav=arrow", "?nav=<script>"):
        assert "nav-hints" not in client.get(f"/{query}", headers=hosted).data.decode()


def test_nav_hints_do_not_reach_the_canonical(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """A hinted URL must still name the plain one, or `?nav=` variants land
    in the index as duplicates."""
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/2026-05-15?nav=chevron").data.decode()
    assert '<link rel="canonical" href="http://localhost/2026-05-15">' in body


def test_nav_links_are_marked_up_for_styling(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """The two ways out of the day view — into the month, into the about
    page — are the links the hint styles have to be able to reach. The
    about link lives in the footer, which only renders once the cache has
    a timestamp to show."""
    import time as _time

    main._cache["events"] = []
    main._cache["fetched_at"] = _time.time()
    monkeypatch.setattr(main, "_events", lambda: [])
    body = main.app.test_client().get("/").data.decode()
    assert 'class="plain nav-link" href="/calendar/2026-05"' in body
    assert 'class="nav-link" href="/about"' in body
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0


def test_robots_keeps_the_calendar_out_of_search() -> None:
    body = main.app.test_client().get("/robots.txt").data.decode()
    assert "Disallow: /calendar/" in body
