import os
import threading
import time
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

import yaml
from flask import Flask, render_template
from markupsafe import Markup, escape
from werkzeug.routing import BaseConverter, ValidationError

from sportsball.adapters import giants, warriors
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
    return Markup(safe)


_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"events": None, "fetched_at": 0.0}


def _adapters() -> list:
    year = datetime.now(tz=PT).year
    return [
        lambda: giants.fetch_events(season=year),
        warriors.fetch_events,
    ]


def _events() -> list[Event]:
    with _cache_lock:
        if _cache["events"] is None or time.time() - _cache["fetched_at"] > CACHE_TTL_SECONDS:
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
def index(verb: str = "hosed", isodate: date | None = None) -> str:
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
    return render_template(
        "8ball.html",
        verb=verb,
        fucked=bool(status.today_events),
        status=status,
        giants_active=any(e.venue == "Oracle Park" for e in status.today_events),
        warriors_active=any(e.venue == "Chase Center" for e in status.today_events),
        verb_class=_verb_color_class(status.today_events),
        quiet_label=quiet_label,
        next_event_label=next_event_label,
    )


@app.get("/healthz")
def healthz() -> tuple[str, int]:
    return "ok", 200
