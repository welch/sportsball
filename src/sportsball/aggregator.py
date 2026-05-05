import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sportsball.models import Event

PT = ZoneInfo("America/Los_Angeles")
TRACKED_VENUES = frozenset({"Oracle Park", "Chase Center"})

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
