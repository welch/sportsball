import hashlib
import logging
import os
import threading
import time
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

import yaml
from flask import Flask, abort, redirect, render_template, request, url_for
from markupsafe import Markup, escape
from werkzeug.routing import BaseConverter, ValidationError
from werkzeug.wrappers import Response

from sportsball import stats, store
from sportsball.adapters import giants, ticketmaster, warriors
from sportsball.aggregator import PT, compute_status, fetch_all
from sportsball.models import Event

ENV_YAML_PATH = Path(__file__).resolve().parents[2] / "env.yaml"
CACHE_TTL_SECONDS = 12 * 3600


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


class VerbConverter(BaseConverter):
    """Letters-only verb path segment, so `/2026-05-15` can't be misread as a verb."""

    regex = r"[a-zA-Z]+"


app = Flask(__name__)
app.url_map.converters["isodate"] = IsoDateConverter
app.url_map.converters["verb"] = VerbConverter

log = logging.getLogger(__name__)


@app.before_request
def _redirect_to_canonical_host() -> Response | None:
    """301-redirect non-canonical hosts to `CANONICAL_HOST`.

    No-ops when `CANONICAL_HOST` is unset (local dev). Skips `/healthz` and
    GAE cron requests so they keep working on the appspot host.
    """
    canonical = os.environ.get("CANONICAL_HOST")
    if not canonical or request.host == canonical:
        return None
    if request.path == "/healthz":
        return None
    if request.headers.get("X-Appengine-Cron") == "true":
        return None
    return redirect(f"https://{canonical}{request.full_path}", code=301)


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


_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"events": None, "fetched_at": 0.0}


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


def _events() -> list[Event]:
    """Return the cached event list, refreshing from storage when stale.

    Cache miss → try Cloud Storage first (cron writes a snapshot daily).
    If the blob is missing or unreadable, fall back to fetching adapters
    directly so local dev (no `EVENTS_BUCKET` set) keeps working.
    """
    with _cache_lock:
        stale = _cache["events"] is None or time.time() - _cache["fetched_at"] > CACHE_TTL_SECONDS
        if stale:
            stored = store.read_events()
            if stored is not None:
                events, fetched_at = stored
                _cache["events"] = events
                _cache["fetched_at"] = fetched_at.timestamp()
            else:
                _cache["events"] = fetch_all(_adapters())
                _cache["fetched_at"] = time.time()
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
    if not today_events:
        return ""
    categories = {e.category for e in today_events}
    if categories == {"concert"}:
        return "concert"
    if categories == {"sports"}:
        venues = {e.venue for e in today_events}
        if venues == {"Oracle Park"}:
            return "giants"
        if venues == {"Chase Center"}:
            return "warriors"
    return ""


@app.get("/")
@app.get("/<verb:verb>/")
@app.get("/<isodate:isodate>")
@app.get("/<verb:verb>/<isodate:isodate>")
def index(verb: str | None = None, isodate: date | None = None) -> str:
    if verb is None:
        verb = os.environ.get("VERB", "hosed")
    if isodate is not None:
        now = datetime.combine(isodate, dtime(12, 0), tzinfo=PT)
    else:
        now = datetime.now(tz=PT)
    status = compute_status(_events(), now)
    quiet_label = (
        _format_day_label(status.next_quiet_date, status.today)
        if status.next_quiet_date and status.today_events
        else None
    )
    next_event_label = (
        _format_day_label(status.next_event_date, status.today) if status.next_event_date else None
    )
    # Halo only reflects today's events — future-event days draw a bare ball.
    # Halo color follows category + venue:
    #   sports at Oracle Park → giants halo (orange)
    #   sports at Chase Center → warriors halo (blue) (also covers Valkyries)
    #   concert at either venue → concert halo (purple)
    return render_template(
        "8ball.html",
        verb=verb,
        fucked=bool(status.today_events),
        status=status,
        giants_active=any(
            e.venue == "Oracle Park" and e.category == "sports" for e in status.today_events
        ),
        warriors_active=any(
            e.venue == "Chase Center" and e.category == "sports" for e in status.today_events
        ),
        concert_active=any(e.category == "concert" for e in status.today_events),
        verb_class=_verb_color_class(status.today_events),
        quiet_label=quiet_label,
        next_event_label=next_event_label,
        last_updated=_last_updated_label(),
    )


def _last_updated_label() -> str | None:
    ts = _cache["fetched_at"]
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=PT).strftime("%a %b %-d, %-I:%M %p %Z")


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


def _is_health_path(path: str) -> bool:
    return path.startswith("/health/")


@app.after_request
def _record_request_end(response: Any) -> Any:
    """Tally each completed response into the rolling 24-hour stats deque.

    The health page is intentionally excluded so reloading it doesn't
    inflate its own counters.
    """
    if not _is_health_path(request.path):
        stats.record_request(response.status_code)
    return response


@app.get("/healthz")
def healthz() -> tuple[str, int]:
    return "ok", 200


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
    now = datetime.now(tz=PT)
    cache_fetched_ts = _cache["fetched_at"] or 0.0
    cache_fetched_at = datetime.fromtimestamp(cache_fetched_ts, tz=PT) if cache_fetched_ts else None
    cache_age_label: str | None = None
    if cache_fetched_at is not None:
        cache_age_label = _humanize_age((now - cache_fetched_at).total_seconds())
    cached_events = _cache["events"] or []
    return render_template(
        "health.html",
        now=now,
        adapters=stats.adapter_stats(ADAPTER_NAMES),
        request_summary=stats.request_summary(),
        cache_event_count=len(cached_events),
        cache_fetched_at=cache_fetched_at,
        cache_age_label=cache_age_label,
        humanize_age=_humanize_age,
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
    events = fetch_all(_adapters())
    fetched_at = datetime.now(tz=PT)
    try:
        store.write_events(events, fetched_at)
    except Exception:
        log.exception("storage write failed; cron continued with local cache only")
    with _cache_lock:
        _cache["events"] = events
        _cache["fetched_at"] = fetched_at.timestamp()
    return f"refreshed: {len(events)} events\n", 200
