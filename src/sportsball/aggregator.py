import calendar as _calendar
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sportsball.models import Event

PT = ZoneInfo("America/Los_Angeles")
TRACKED_VENUES = frozenset({"Oracle Park", "Chase Center"})

# Hue per venue — the one mapping behind every colored thing on the site:
# day rings, the verb, and venue names in event descriptions. The class
# names are historical (they're the team colors), but what they encode is
# the building, not who's playing in it.
VENUE_COLORS = {"Oracle Park": "giants", "Chase Center": "warriors"}

# Calendar grids run Sunday-first, US convention.
_CAL = _calendar.Calendar(firstweekday=6)
WEEKDAY_LABELS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")

EventFetcher = Callable[[], list[Event]]
NamedAdapter = tuple[str, EventFetcher]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Status:
    today: date
    today_events: list[Event] = field(default_factory=list)
    next_event_date: date | None = None
    next_event_events: list[Event] = field(default_factory=list)
    next_quiet_date: date | None = None


@dataclass(frozen=True)
class CalendarDay:
    """One cell of a month grid."""

    day: date
    in_month: bool
    is_today: bool
    events: list[Event] = field(default_factory=list)
    halos: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MonthView:
    month: date  # first of the displayed month
    prev_month: date
    next_month: date
    weeks: list[list[CalendarDay]] = field(default_factory=list)


def day_halos(events: Iterable[Event]) -> list[str]:
    """CSS marker classes for a day's events, innermost ring first.

    The single source of truth for "what does this day look like?" — the
    8-ball's halo and the calendar's day rings both read from here, so a day
    can never be orange on one page and blue on the other.

    Two channels, two questions. Hue answers *where*: orange for Oracle
    Park, blue for Chase Center. Texture answers *what*: a soft glow
    (`halo-*`) means the home team is playing, a dashed ring (`ring-*`)
    means something else has the building. They compose — a Giants game and
    a concert at Oracle Park on the same day is an orange glow with an
    orange dashed ring through it.

    Keeping venue on the hue means a third venue would cost one color
    rather than two, and it keeps the palette inside what stays legible at
    a 25px calendar cell.
    """
    halos = []
    if any(e.venue == "Oracle Park" and e.kind == "home" for e in events):
        halos.append("halo-giants")
    if any(e.venue == "Oracle Park" and e.kind == "event" for e in events):
        halos.append("ring-oracle")
    if any(e.venue == "Chase Center" and e.kind == "home" for e in events):
        halos.append("halo-warriors")
    if any(e.venue == "Chase Center" and e.kind == "event" for e in events):
        halos.append("ring-chase")
    return halos


def month_view(events: list[Event], month: date, today: date) -> MonthView:
    """Build a Sunday-first grid of whole weeks covering ``month``.

    Leading/trailing cells belong to the adjacent months — they're marked
    ``in_month=False`` so the template can dim them, but they still carry
    their own events and stay clickable, since a game on the 1st is just
    as real when you're looking at the previous month.
    """
    by_date: dict[date, list[Event]] = {}
    for e in events:
        by_date.setdefault(e.starts_at.astimezone(PT).date(), []).append(e)
    weeks = [
        [
            CalendarDay(
                day=d,
                in_month=d.month == month.month and d.year == month.year,
                is_today=d == today,
                events=sorted(by_date.get(d, []), key=lambda e: e.starts_at),
                halos=day_halos(by_date.get(d, [])),
            )
            for d in week
        ]
        for week in _CAL.monthdatescalendar(month.year, month.month)
    ]
    return MonthView(
        month=month,
        prev_month=_shift_month(month, -1),
        next_month=_shift_month(month, +1),
        weeks=weeks,
    )


def _shift_month(month: date, delta: int) -> date:
    """First of the month ``delta`` months away from ``month``."""
    index = month.year * 12 + (month.month - 1) + delta
    return date(index // 12, index % 12 + 1, 1)


def fetch_all(adapters: list[NamedAdapter]) -> list[Event]:
    """Run each named adapter, recording success/failure to `stats`.

    Each adapter gets a stable `name` (e.g. ``"giants.fetch_events"``) so
    the health endpoint can report per-source timestamps. One failing
    source still doesn't blank the page — the failure is logged and
    recorded, and the remaining adapters' events render.
    """
    # Imported lazily to keep `aggregator` importable from `stats` without
    # introducing a circular import.
    from sportsball import stats

    events: list[Event] = []
    for name, fetch in adapters:
        try:
            fetched = fetch()
        except Exception as exc:
            log.exception("adapter %s failed", name)
            stats.record_adapter_failure(name, f"{type(exc).__name__}: {exc}")
            continue
        kept = [e for e in fetched if e.venue in TRACKED_VENUES]
        stats.record_adapter_success(name, len(kept))
        events.extend(kept)
    return events


def compute_status(events: list[Event], now: datetime) -> Status:
    today = now.astimezone(PT).date()
    today_events: list[Event] = []
    future_by_date: dict[date, list[Event]] = {}
    for e in events:
        local_date = e.starts_at.astimezone(PT).date()
        if local_date == today:
            today_events.append(e)
        elif local_date > today:
            future_by_date.setdefault(local_date, []).append(e)
    today_events.sort(key=lambda e: e.starts_at)
    next_date = min(future_by_date) if future_by_date else None
    next_events = sorted(future_by_date[next_date], key=lambda e: e.starts_at) if next_date else []
    next_quiet = _next_quiet_date(today, today_events, future_by_date)
    return Status(
        today=today,
        today_events=today_events,
        next_event_date=next_date,
        next_event_events=next_events,
        next_quiet_date=next_quiet,
    )


def _next_quiet_date(
    today: date,
    today_events: list[Event],
    future_by_date: dict[date, list[Event]],
) -> date | None:
    """First date on or after today with no tracked-venue events.

    Returns today if today has no events. Otherwise walks forward day by day
    through the future-events map until it finds a gap. Returns None only if
    we somehow have events stretching past one year (defensive).
    """
    if not today_events:
        return today
    candidate = today + timedelta(days=1)
    horizon = today + timedelta(days=365)
    while candidate <= horizon:
        if candidate not in future_by_date:
            return candidate
        candidate += timedelta(days=1)
    return None
