import hashlib
import logging
import os
import random
import threading
import time
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any, NamedTuple

import yaml
from flask import Flask, abort, redirect, render_template, request, url_for
from markupsafe import Markup, escape
from werkzeug.routing import BaseConverter, ValidationError
from werkzeug.wrappers import Response

from sportsball import stats, store
from sportsball.adapters import giants, ticketmaster, warriors
from sportsball.aggregator import (
    PT,
    VENUE_COLORS,
    WEEKDAY_LABELS,
    compute_status,
    day_halos,
    fetch_all,
    month_view,
)
from sportsball.models import Event

ENV_YAML_PATH = Path(__file__).resolve().parents[2] / "env.yaml"
CACHE_TTL_SECONDS = 12 * 3600
# How far either side of today the browsable date space extends, in whole
# calendar years. The calendar used to step forever in both directions —
# defensible for a human, fatal with a crawler: in Aug 2026 GPTBot followed
# the next-month chevron out to year 9241 and sustained ~7k requests/hour,
# which is also what pushed the health page's 24h log scan past gunicorn's
# timeout. A year either side is far past any adapter's horizon (MLB and NBA
# publish one season; Ticketmaster about a year), so nobody browsing in good
# faith reaches the edge, and a crawler that does finds no link to follow.
BROWSE_YEARS = 1
# How often a warm instance asks GCS whether the snapshot blob has been
# overwritten. Cheap enough (a metadata GET, no payload) to run at this
# cadence, and it's what makes a `bin/refresh` land on instances that are
# already warm instead of waiting out `CACHE_TTL_SECONDS`.
SNAPSHOT_POLL_SECONDS = 60

# MLB cities (current homes — Athletics moved to Sacramento for 2025-26
# while they wait on their Las Vegas park). San Francisco is intentionally
# excluded since the about page is asking "what about other cities?"
MLB_CITIES = (
    "Anaheim",
    "Arlington",
    "Atlanta",
    "Baltimore",
    "Boston",
    "Chicago",
    "Cincinnati",
    "Cleveland",
    "Denver",
    "Detroit",
    "Houston",
    "Kansas City",
    "Los Angeles",
    "Miami",
    "Milwaukee",
    "Minneapolis",
    "New York",
    "Philadelphia",
    "Phoenix",
    "Pittsburgh",
    "Sacramento",
    "San Diego",
    "Seattle",
    "St. Louis",
    "Tampa",
    "Toronto",
    "Washington",
)


def _load_env_yaml(path: Path = ENV_YAML_PATH) -> None:
    if not path.exists():
        return
    data: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    for key, value in (data.get("env_variables") or {}).items():
        os.environ.setdefault(key, str(value))


_load_env_yaml()


class IsoDateConverter(BaseConverter):
    """Match YYYY-MM-DD path segments and parse to a `date`."""

    regex = r"\d{4}-\d{2}-\d{2}"

    def to_python(self, value: str) -> date:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError() from exc

    def to_url(self, value: date) -> str:
        return value.isoformat()


class MonthConverter(BaseConverter):
    """Match YYYY-MM path segments and parse to the first of that month.

    Deliberately narrower than `isodate` so `/2026-05` and `/2026-05-15`
    can't be confused for one another.
    """

    regex = r"\d{4}-\d{2}"

    def to_python(self, value: str) -> date:
        year, _, month = value.partition("-")
        try:
            return date(int(year), int(month), 1)
        except ValueError as exc:
            raise ValidationError() from exc

    def to_url(self, value: date) -> str:
        return f"{value.year:04d}-{value.month:02d}"


app = Flask(__name__)
app.url_map.converters["isodate"] = IsoDateConverter
app.url_map.converters["month"] = MonthConverter

log = logging.getLogger(__name__)


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}


def _host_map(var: str) -> dict[str, str]:
    """Parse a `host=value, host=value` environment variable into a map.

    The grammar both per-domain settings share (`HOST_VERBS`, `NAV_HINTS`).
    Hosts are lowercased for matching; malformed entries are dropped rather
    than raised on, so a typo in one domain doesn't take the site down for
    the others.

    Read per request rather than frozen into a constant, for the reason
    `_repo_url` explains: `env.yaml` lands in `os.environ` at import.
    """
    mapping: dict[str, str] = {}
    for chunk in os.environ.get(var, "").split(","):
        host, sep, value = chunk.partition("=")
        host, value = host.strip().lower(), value.strip()
        if sep and host and value:
            mapping[host] = value
    return mapping


def _host_verbs() -> dict[str, str]:
    """The domains this app answers to, and the verb each one renders:
    `"ismydayfucked.com=fucked, ismydayhosed.fun=hosed"`.

    One deployment serves every domain in the map. The pages differ only in
    the word, which is the whole point: the same site can be linked from a
    résumé without the profanity, or from anywhere else with it.

    Order matters. The first entry is the primary host — where unrecognized
    hosts get redirected, and whose verb an unmapped host renders.
    """
    return _host_map("HOST_VERBS")


def _request_host() -> str:
    """Requested hostname, lowercased with any port stripped, for map lookups."""
    return request.host.split(":", 1)[0].lower()


# Nav-hint styles the CSS knows how to draw. The names are the contract
# between `$NAV_HINTS` and `8ball.css`; adding one means adding a
# `.nav-<name>` block there and a word here. Anything else — an unknown
# style, `?nav=off` — renders no hints at all, which is the default look.
NAV_HINT_STYLES = ("chevron",)


def _nav_hint_class() -> str:
    """Body class that turns on visible affordances for `.nav-link` elements,
    or "" for the default look.

    The site's links are deliberately undressed — a blue underline would
    wreck the hand-drawn hand — but that leaves a first-time visitor with
    nothing saying the page responds at all. On a domain being handed to
    someone who has never seen the site (a résumé link), the affordance is
    worth more than the restraint, so it's per-host: `$NAV_HINTS` reads
    `"ismydayhosed.fun=chevron"`.

    `?nav=<style>` overrides for a request, which is how you compare styles
    on one page without editing `env.yaml` and restarting. It doesn't leak
    into search: the canonical tag is built from the path alone, so a
    hinted URL still names the plain one.
    """
    style = request.args.get("nav") or _host_map("NAV_HINTS").get(_request_host(), "")
    return f"nav-hints nav-{style}" if style in NAV_HINT_STYLES else ""


def _default_verb() -> str:
    """The verb this request's host asks for.

    A mapped host gets its own verb. Anything else — localhost, appspot,
    a health check — gets the primary host's, so local dev shows what the
    live site shows. With no map configured at all, the neutral `hosed`.
    """
    hosts = _host_verbs()
    return hosts.get(_request_host()) or next(iter(hosts.values()), "hosed")


@app.before_request
def _redirect_to_known_host() -> Response | None:
    """301-redirect hosts outside `$HOST_VERBS` to the domain they belong to.

    The map's own domains each serve themselves. A `www.` prefix belongs to
    the domain under it, so `www.ismydayhosed.fun` lands on
    `ismydayhosed.fun` rather than being folded onto the primary — sending
    someone who deliberately typed the polite name across to the profane one
    would be a rude surprise. Everything else (appspot, a bare IP, a stale
    alias, and `www.` of a domain we don't serve) still consolidates onto the
    first entry.

    No-ops when `HOST_VERBS` is unset (local dev). Also skips localhost
    addresses, so a developer whose local `env.yaml` carries the production
    map can still hit `http://localhost:5000` without bouncing to
    production. Skips `/healthz` and GAE cron requests so they keep working
    on the appspot host.
    """
    hosts = _host_verbs()
    if not hosts:
        return None
    host = _request_host()
    if host in hosts or host in _LOCAL_HOSTS:
        return None
    if request.path == "/healthz":
        return None
    if request.headers.get("X-Appengine-Cron") == "true":
        return None
    bare = host.removeprefix("www.")
    target = bare if bare in hosts else next(iter(hosts))
    return redirect(f"https://{target}{request.full_path}", code=301)


def _compute_static_hash() -> str:
    """Hash of the bundled static files. Stable per deploy; changes when any
    asset (CSS/img/template-referenced file) changes, busting browser caches.
    """
    static_dir = Path(app.static_folder) if app.static_folder else None
    if not static_dir or not static_dir.exists():
        return "0"
    h = hashlib.sha256()
    for path in sorted(static_dir.rglob("*")):
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()[:12]


STATIC_HASH = _compute_static_hash()


@app.context_processor
def _static_helpers() -> dict[str, Any]:
    def vstatic(filename: str) -> str:
        return f"{url_for('static', filename=filename)}?v={STATIC_HASH}"

    return {"vstatic": vstatic}


@app.template_filter("pt")
def to_pt(dt: datetime) -> datetime:
    return dt.astimezone(PT)


@app.template_filter("team_colorize")
def team_colorize(text: str) -> Markup:
    """Wrap full team names in team-color spans; everything else stays."""
    safe = escape(text)
    safe = safe.replace(
        "San Francisco Giants",
        Markup('<span class="giants">San Francisco Giants</span>'),
    )
    safe = safe.replace(
        "Golden State Warriors",
        Markup('<span class="warriors">Golden State Warriors</span>'),
    )
    # Valkyries share Chase Center with the Warriors → same blue.
    safe = safe.replace(
        "Golden State Valkyries",
        Markup('<span class="warriors">Golden State Valkyries</span>'),
    )
    return Markup(safe)


@app.template_filter("venue_colorize")
def venue_colorize(venue: str) -> Markup:
    """Tint a venue name with its own hue, same as the rings use.

    An untracked venue (an away game that slipped through, a renamed
    building) renders plain rather than guessing at a color.
    """
    safe = escape(venue)
    color = VENUE_COLORS.get(venue)
    if not color:
        return Markup(safe)
    return Markup('<span class="{}">{}</span>').format(color, safe)


_cache_lock = threading.Lock()
_cache: dict[str, Any] = {
    "events": None,
    "fetched_at": 0.0,
    "previously_unseen": [],
    # Generation of the storage blob these events came from, and when we
    # last asked GCS whether that's still the current one. Both stay unset
    # on the adapter-fallback path, where there's no blob to compare to.
    "generation": None,
    "checked_at": 0.0,
}


ADAPTER_NAMES: tuple[str, ...] = (
    "giants.fetch_events",
    "warriors.fetch_events",
    "ticketmaster.fetch_oracle_park_events",
    "ticketmaster.fetch_chase_center_events",
)


def _adapters() -> list[tuple[str, Any]]:
    year = datetime.now(tz=PT).year
    return [
        ("giants.fetch_events", lambda: giants.fetch_events(season=year)),
        ("warriors.fetch_events", warriors.fetch_events),
        ("ticketmaster.fetch_oracle_park_events", ticketmaster.fetch_oracle_park_events),
        ("ticketmaster.fetch_chase_center_events", ticketmaster.fetch_chase_center_events),
    ]


def _snapshot_replaced(now: float) -> bool:
    """Has the storage blob been overwritten since we loaded it?

    Rate-limited to one metadata call per `SNAPSHOT_POLL_SECONDS` so a busy
    instance doesn't ask GCS on every request. Caller holds `_cache_lock`.

    An unknown generation counts as "no" — see `store.current_generation`.
    That also covers the no-bucket case, where the check costs nothing and
    the 12-hour TTL remains the only trigger.
    """
    if now - _cache["checked_at"] < SNAPSHOT_POLL_SECONDS:
        return False
    _cache["checked_at"] = now
    generation = store.current_generation()
    return generation is not None and generation != _cache["generation"]


def _events() -> list[Event]:
    """Return the cached event list, refreshing from storage when stale.

    Three things make the cache stale: nothing loaded yet, a snapshot older
    than `CACHE_TTL_SECONDS`, or the cron (or `bin/refresh`) having written
    a new blob since we loaded ours. That last check is what lets a manual
    refresh reach warm instances promptly rather than waiting out the TTL,
    which matters while the event data is still being shaken out.

    Cache miss → try Cloud Storage first (cron writes a snapshot daily).
    If the blob is missing or unreadable, fall back to fetching adapters
    directly so local dev (no `EVENTS_BUCKET` set) keeps working.
    """
    with _cache_lock:
        now = time.time()
        stale = (
            _cache["events"] is None
            or now - _cache["fetched_at"] > CACHE_TTL_SECONDS
            or _snapshot_replaced(now)
        )
        if stale:
            # Generation before body, deliberately. If the blob is rewritten
            # between the two calls we record the older generation and reload
            # once more at the next poll — a wasted read. The other ordering
            # would file a payload under a generation newer than itself and
            # sit on stale events until the TTL expired.
            generation = store.current_generation()
            stored = store.read_events()
            if stored is not None:
                events, fetched_at, prev_unseen, adapter_snapshot = stored
                _cache["events"] = events
                _cache["fetched_at"] = fetched_at.timestamp()
                _cache["previously_unseen"] = prev_unseen
                _cache["generation"] = generation
                if adapter_snapshot:
                    stats.load_adapter_stats(adapter_snapshot)
            else:
                _cache["events"] = fetch_all(_adapters())
                _cache["fetched_at"] = now
                _cache["previously_unseen"] = []
                _cache["generation"] = None
        return _cache["events"]


def _format_day_label(d: date, today: date) -> str:
    days_away = (d - today).days
    if days_away == 0:
        return "today"
    if days_away == 1:
        return "tomorrow"
    if days_away <= 7:
        return d.strftime("%A")
    return d.strftime("%A, %b %-d")


def _verb_color_class(today_events: list[Event]) -> str:
    """Color the verb when one venue owns the day, neutral when both do.

    Venue, not team — a concert at Oracle Park colors the verb orange for
    the same reason it draws an orange ring: what wrecks your day is which
    neighborhood fills up, not who's playing.
    """
    if not today_events:
        return ""
    venues = {e.venue for e in today_events}
    if len(venues) != 1:
        return ""
    return VENUE_COLORS.get(next(iter(venues)), "")


def _canonical_url(endpoint: str, **values: Any) -> str:
    """Absolute URL for `endpoint` on the host that should own this page.

    Each domain in `$HOST_VERBS` self-canonicalizes. The alternative —
    folding every domain onto the primary — would consolidate the search
    signal, but it would also mean someone who found the polite domain in
    search got pointed at the impolite one, which defeats the reason the
    polite one exists. Near-duplicates across two hosts are a small price;
    the rendered verb differs on every page anyway.

    An unmapped host (appspot.com, an IP, a stale alias) names the primary
    domain instead of itself, so a page served on one of those still points
    at the real site. Falls back to the request host when no map is
    configured, for local dev.
    """
    hosts = _host_verbs()
    if not hosts:
        return url_for(endpoint, _external=True, **values)
    host = _request_host() if _request_host() in hosts else next(iter(hosts))
    return f"https://{host}{url_for(endpoint, **values)}"


def _browse_range() -> tuple[date, date]:
    """Inclusive first/last date the site will render, `BROWSE_YEARS` either
    side of today rounded out to whole calendar years.

    Whole years rather than a rolling 365 days so the bound doesn't drift
    mid-month and turn a URL somebody bookmarked yesterday into a 404 today.
    """
    year = datetime.now(tz=PT).year
    return date(year - BROWSE_YEARS, 1, 1), date(year + BROWSE_YEARS, 12, 31)


def _in_browse_range(d: date) -> bool:
    earliest, latest = _browse_range()
    return earliest <= d <= latest


@app.get("/")
@app.get("/<isodate:isodate>")
def index(isodate: date | None = None) -> str:
    verb = _default_verb()
    if isodate is not None:
        if not _in_browse_range(isodate):
            abort(404)
        now = datetime.combine(isodate, dtime(12, 0), tzinfo=PT)
    else:
        now = datetime.now(tz=PT)
    status = compute_status(_events(), now)
    halos = day_halos(status.today_events)
    quiet_label = (
        _format_day_label(status.next_quiet_date, status.today)
        if status.next_quiet_date and status.today_events
        else None
    )
    next_event_label = (
        _format_day_label(status.next_event_date, status.today) if status.next_event_date else None
    )
    # Rings only reflect today's events — future-event days draw a bare ball.
    # See `aggregator.day_halos` for what the colors and textures mean.
    return render_template(
        "8ball.html",
        verb=verb,
        fucked=bool(status.today_events),
        status=status,
        halos=halos,
        verb_class=_verb_color_class(status.today_events),
        quiet_label=quiet_label,
        next_event_label=next_event_label,
        nav_hint_class=_nav_hint_class(),
        calendar_url=url_for("month_calendar", ym=status.today.replace(day=1)),
        canonical_url=(
            _canonical_url("index", isodate=isodate)
            if isodate is not None
            else _canonical_url("index")
        ),
    )


@app.get("/calendar/")
@app.get("/calendar/<month:ym>")
def month_calendar(ym: date | None = None) -> str:
    """Month grid where each day wears the same colored rings as the 8-ball.

    Clicking a day drops into the 8-ball view for that date; the chevrons
    step a month at a time within `_browse_range` — empty months are a
    perfectly good answer, but the walk has to stop somewhere or a crawler
    takes it to the heat death of the universe (see `BROWSE_YEARS`). At the
    edges the chevron and the spill-over day cells render as dead text
    rather than links, so there's nothing to follow past the boundary.
    """
    today = datetime.now(tz=PT).date()
    month = ym if ym is not None else today.replace(day=1)
    if not _in_browse_range(month):
        abort(404)
    view = month_view(_events(), month, today)

    def month_url(target: date) -> str | None:
        return url_for("month_calendar", ym=target) if _in_browse_range(target) else None

    def day_url(d: date) -> str | None:
        return url_for("index", isodate=d) if _in_browse_range(d) else None

    return render_template(
        "calendar.html",
        verb=_default_verb(),
        view=view,
        weekday_labels=WEEKDAY_LABELS,
        month_label=month.strftime("%B %Y"),
        prev_url=month_url(view.prev_month),
        next_url=month_url(view.next_month),
        prev_label=view.prev_month.strftime("%B %Y"),
        next_label=view.next_month.strftime("%B %Y"),
        home_url=url_for("index"),
        day_url=day_url,
        nav_hint_class=_nav_hint_class(),
        # Bare month URL even when `ym` came from the bare `/calendar/`
        # route, so `/calendar/` and `/calendar/2026-08` don't compete.
        canonical_url=_canonical_url("month_calendar", ym=month),
    )


def _last_updated_label() -> str | None:
    ts = _cache["fetched_at"]
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=PT).strftime("%a %b %-d, %-I:%M %p %Z")


class VersionInfo(NamedTuple):
    """A rendered version string plus where it points, if anywhere.

    `url` is the commit page when we know both the repo and a SHA, the
    repo root when we know only the repo, and None when `$REPO_URL` is
    unset — a fork that hasn't pointed this at its own repo shows a plain
    build string rather than linking the operator into someone else's
    source.
    """

    label: str
    url: str | None


def _repo_url() -> str:
    """Base URL of the source repo, from `$REPO_URL`; "" when unconfigured.

    Read per call rather than frozen into a module constant: `env.yaml`
    is loaded at import (`_load_env_yaml`), and a constant defined above
    that call would capture the environment before it lands.
    """
    return os.environ.get("REPO_URL", "").rstrip("/")


def _version_info() -> VersionInfo:
    """Describe the running build for the health page.

    `bin/deploy` encodes git state into the version ID as
    `<tag>-<sha>` for clean trees and `<tag>-<sha>-dirty` for dirty ones,
    with dots in the tag mangled to hyphens. Convert back to a readable
    form like `v0.4.0+dc9473a` (or with " (dirty)" appended), and point the
    label at that commit in `$REPO_URL`. If the instance was deployed
    without `bin/deploy` (timestamp-style ID) or we're in local dev (env
    var unset), fall back gracefully to the raw ID and the repo root.

    A dirty deploy still links its commit: the running code isn't exactly
    that tree, which is what the "(dirty)" marker is there to say.
    """
    repo = _repo_url()
    raw = os.environ.get("GAE_VERSION")
    if not raw:
        return VersionInfo("(local — GAE_VERSION not set)", repo or None)
    parts = raw.split("-")
    if len(parts) >= 3 and parts[-1] == "dirty":
        tag = ".".join(parts[:-2])
        sha = parts[-2]
        return VersionInfo(f"{tag}+{sha} (dirty)", _commit_url(repo, sha))
    if len(parts) >= 2 and _looks_like_short_sha(parts[-1]):
        tag = ".".join(parts[:-1])
        sha = parts[-1]
        return VersionInfo(f"{tag}+{sha}", _commit_url(repo, sha))
    # Auto-generated timestamp ID — show it raw so the operator sees it.
    return VersionInfo(raw, repo or None)


def _commit_url(repo: str, sha: str) -> str | None:
    """GitHub-style commit permalink, or None when there's no repo to point at."""
    return f"{repo}/commit/{sha}" if repo else None


def _looks_like_short_sha(s: str) -> bool:
    """Lower-hex string between 6 and 12 chars (typical short-SHA range)."""
    return 6 <= len(s) <= 12 and all(c in "0123456789abcdef" for c in s)


def _humanize_age(seconds: float) -> str:
    """Render a non-negative duration as e.g. ``"3 minutes ago"``.

    Picks the largest reasonable unit and rounds: seconds under a minute,
    minutes under an hour, hours (one decimal) under a day, then days.
    """
    if seconds < 0:
        seconds = 0.0
    if seconds < 60:
        n = int(seconds)
        unit = "second" if n == 1 else "seconds"
        return f"{n} {unit} ago"
    if seconds < 3600:
        n = int(seconds // 60)
        unit = "minute" if n == 1 else "minutes"
        return f"{n} {unit} ago"
    if seconds < 86400:
        hours = seconds / 3600
        unit = "hour" if 0.95 <= hours < 1.05 else "hours"
        return f"{hours:.1f} {unit} ago"
    days = seconds / 86400
    unit = "day" if 0.95 <= days < 1.05 else "days"
    return f"{days:.1f} {unit} ago"


@app.get("/healthz")
def healthz() -> tuple[str, int]:
    return "ok", 200


# Operator endpoints, plus the month view — it exists to be clicked from the
# day page, not found cold in search results, and it's the densest part of
# the crawlable space.
#
# Day views stay crawlable. `Crawl-delay` is advisory and Google ignores it
# outright, so the real defence remains `_browse_range`, not this file.
ROBOTS_TXT = """User-agent: *
Disallow: /health/
Disallow: /healthz
Disallow: /tasks/
Disallow: /calendar/
Crawl-delay: 10
"""


@app.get("/robots.txt")
def robots() -> Response:
    return Response(ROBOTS_TXT, mimetype="text/plain")


@app.get("/about")
def about() -> str:
    """Plain-language explanation of what the site does. The footer links
    here so casual visitors don't get dumped straight into a code repo.
    """
    # The footer used to carry the refresh time on every page, where it
    # read as noise. It lives here now, which means this page has to load
    # the snapshot itself — the day and month views populate the cache on
    # their way past, and a visitor can land here first. Same reasoning as
    # `/health/<token>`, and the same one-per-instance cost.
    if _cache["events"] is None:
        _events()
    return render_template(
        "about.html",
        random_city=random.choice(MLB_CITIES),
        last_updated=_last_updated_label(),
        canonical_url=_canonical_url("about"),
    )


@app.get("/health/<token>")
def health(token: str) -> str:
    """Token-gated status page. Wrong token 404s — never reveals existence.

    Renders an HTML snapshot of: per-adapter last success/failure, the
    24-hour HTTP request counters, and current cache contents. Idle (no
    auto-refresh); the user reloads when they want fresh numbers.
    """
    expected = os.environ.get("HEALTH_TOKEN")
    if not expected or token != expected:
        abort(404)
    # On a truly-fresh instance (no previous request has populated the
    # cache yet), trigger the storage load so the adapter snapshot from
    # the latest cron is loaded into stats. Without this, a fresh
    # instance whose first hit is /health would show every adapter as
    # "never". Skip when the cache already has entries — even an empty
    # list signals "we've loaded already, just no events."
    if _cache["events"] is None:
        _events()
    now = datetime.now(tz=PT)
    cache_fetched_ts = _cache["fetched_at"] or 0.0
    cache_fetched_at = datetime.fromtimestamp(cache_fetched_ts, tz=PT) if cache_fetched_ts else None
    cache_age_label: str | None = None
    if cache_fetched_at is not None:
        cache_age_label = _humanize_age((now - cache_fetched_at).total_seconds())
    cached_events = _cache["events"] or []
    new_events = sorted(_cache["previously_unseen"] or [], key=lambda e: e.starts_at)
    version = _version_info()
    return render_template(
        "health.html",
        now=now,
        # The header's two facts both link out to their own evidence: the
        # timestamp to what the site was saying that day, the build to the
        # commit it was built from.
        now_url=url_for("index", isodate=now.date()),
        version_label=version.label,
        version_url=version.url,
        adapters=stats.adapter_stats(ADAPTER_NAMES),
        request_summary=stats.request_summary(),
        cache_event_count=len(cached_events),
        new_events=new_events,
        cache_fetched_at=cache_fetched_at,
        cache_age_label=cache_age_label,
        humanize_age=_humanize_age,
        pt=PT,
    )


@app.get("/tasks/refresh")
def refresh() -> tuple[str, int]:
    """Fetch events from all adapters and persist the snapshot.

    Cron is the only writer. Gated by the X-Appengine-Cron header — GAE
    injects this only on cron invocations and strips it from external
    requests, so external callers can't trigger a refetch storm. After
    the storage write, this instance's local cache is updated so the
    "last updated" timestamp the page displays reflects the cron time
    rather than each instance's own first-fetch.
    """
    if request.headers.get("X-Appengine-Cron") != "true":
        abort(403)
    # Load the prior cron's snapshot FIRST so `record_adapter_failure` can
    # preserve historical `last_success_at` for any adapter that fails this
    # run. Without this, a single bad day wipes out "when did this adapter
    # last work?" — exactly what bit us when cdn.nba.com started 403'ing.
    prior = store.read_events()
    if prior is not None:
        stats.load_adapter_stats(prior[3])
    events = fetch_all(_adapters())
    fetched_at = datetime.now(tz=PT)
    prev_events = prior[0] if prior is not None else []
    prev_unseen = store.previously_unseen(events, prev_events)
    # Snapshot per-adapter outcomes so a future serving instance (which will
    # only ever read the storage blob, never run adapters itself) can render
    # the cron's view of adapter health on /health/<token>.
    adapter_snapshot = stats.snapshot_adapter_stats()
    try:
        store.write_events(
            events,
            fetched_at,
            previously_unseen=prev_unseen,
            adapter_stats=adapter_snapshot,
        )
    except Exception:
        log.exception("storage write failed; cron continued with local cache only")
    with _cache_lock:
        _cache["events"] = events
        _cache["fetched_at"] = fetched_at.timestamp()
        _cache["previously_unseen"] = prev_unseen
        # Record the generation we just wrote, so the poll doesn't read our
        # own write back as somebody else's change on the next request.
        _cache["generation"] = store.current_generation()
    return f"refreshed: {len(events)} events ({len(prev_unseen)} new)\n", 200
