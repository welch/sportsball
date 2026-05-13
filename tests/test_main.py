from datetime import datetime
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


def test_index_footer_links_to_about_page(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    import time as _time

    main._cache["events"] = []
    main._cache["fetched_at"] = _time.time()
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/")
    body = response.data
    # Anchor target is now the in-app /about page, not the GitHub repo.
    assert b'href="/about"' in body
    # Anchor wraps the footer text, not a separate node.
    assert b'href="/about">last updated ' in body
    # External-link attributes from the previous version are gone.
    assert b"github.com/welch/sportsball" not in body
    assert b'target="_blank"' not in body
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0


def test_about_page_has_repo_link_and_back_link() -> None:
    response = main.app.test_client().get("/about")
    assert response.status_code == 200
    body = response.data
    assert b"github.com/welch/sportsball" in body
    # Back to today
    assert b'href="/"' in body


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


def test_canonical_host_no_redirect_when_host_matches(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.setenv("CANONICAL_HOST", "example.com")
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get("/", headers={"Host": "example.com"})
    assert response.status_code == 200


def test_canonical_host_redirects_non_canonical_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANONICAL_HOST", "example.com")
    response = main.app.test_client().get("/", headers={"Host": "sports-ball.appspot.com"})
    assert response.status_code == 301
    # Werkzeug normalizes a bare trailing "?" off the Location URL.
    assert response.headers["Location"].rstrip("?") == "https://example.com/"


def test_canonical_host_redirect_preserves_query_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CANONICAL_HOST", "example.com")
    response = main.app.test_client().get(
        "/some/path?foo=bar", headers={"Host": "sports-ball.appspot.com"}
    )
    assert response.status_code == 301
    assert response.headers["Location"].endswith("?foo=bar")
    assert response.headers["Location"] == "https://example.com/some/path?foo=bar"


def test_canonical_host_does_not_redirect_healthz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANONICAL_HOST", "example.com")
    response = main.app.test_client().get("/healthz", headers={"Host": "sports-ball.appspot.com"})
    assert response.status_code == 200
    assert response.data == b"ok"


def test_format_version_unset_env() -> None:
    assert "local" in main._format_version().lower()


def test_format_version_pretty_prints_clean_bin_deploy_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clean trees → no `-clean` suffix; just <tag>-<sha>."""
    monkeypatch.setenv("GAE_VERSION", "v0-4-0-dc9473a")
    assert main._format_version() == "v0.4.0+dc9473a"


def test_format_version_marks_dirty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAE_VERSION", "v0-4-0-dc9473a-dirty")
    assert main._format_version() == "v0.4.0+dc9473a (dirty)"


def test_format_version_falls_back_on_timestamp_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare `gcloud app deploy` (no bin/deploy) gives a timestamp-shaped ID
    that can't be parsed; show it raw so the operator notices."""
    monkeypatch.setenv("GAE_VERSION", "20260507t170000")
    assert main._format_version() == "20260507t170000"


def test_format_version_falls_back_on_unparseable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anything that doesn't end in a short-SHA-shaped trailing part shows raw."""
    monkeypatch.setenv("GAE_VERSION", "manual-deploy")
    assert main._format_version() == "manual-deploy"


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


def test_canonical_host_does_not_redirect_localhost(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    """Dev convenience: localhost requests skip the redirect even when
    CANONICAL_HOST is set, so a dev whose local env.yaml carries the prod
    canonical can still hit http://localhost:PORT without bouncing.
    """
    monkeypatch.setenv("CANONICAL_HOST", "example.com")
    monkeypatch.setattr(main, "_events", lambda: [])
    for host in ("localhost:5071", "127.0.0.1:5071", "localhost", "0.0.0.0:8080"):
        response = main.app.test_client().get("/", headers={"Host": host})
        assert response.status_code == 200, f"unexpected redirect for Host: {host}"


def test_canonical_host_does_not_redirect_cron(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANONICAL_HOST", "example.com")
    monkeypatch.setattr(main, "_events", lambda: [])
    response = main.app.test_client().get(
        "/tasks/refresh",
        headers={"Host": "sports-ball.appspot.com", "X-Appengine-Cron": "true"},
    )
    assert response.status_code == 200
    # Reset cache state for downstream tests.
    main._cache["events"] = None
    main._cache["fetched_at"] = 0.0


def test_canonical_host_unset_never_redirects(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    monkeypatch.delenv("CANONICAL_HOST", raising=False)
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
