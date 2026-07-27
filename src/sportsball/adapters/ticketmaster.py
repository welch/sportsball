import logging
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from sportsball.models import Event

log = logging.getLogger(__name__)

API_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
SOURCE = "ticketmaster_discovery"

# Both tracked venues are in SF; `localDate` is venue-local by definition.
VENUE_TZ = ZoneInfo("America/Los_Angeles")

ORACLE_PARK_VENUE_ID = "KovZpZAJF7EA"
CHASE_CENTER_VENUE_ID = "KovZ917Ah1H"

# subGenres handled by team adapters (MLB Stats API for Giants, NBA CDN for
# Warriors). WNBA Valkyries is a different subGenre so it stays.
SKIP_SUBGENRES = frozenset({"MLB", "NBA"})

PAGE_SIZE = 100
TIMEOUT_SECONDS = 30

# Identify ourselves rather than the default `requests` UA. Helps if
# Ticketmaster's API gateway ever decides to filter unidentified clients,
# and is good hygiene — the API key already authenticates us, no need to
# masquerade as a browser. Accept header narrows the response format.
_HEADERS = {
    "User-Agent": "sportsball/0.5.0 (+https://github.com/welch/sportsball)",
    "Accept": "application/json",
}


def fetch_oracle_park_events() -> list[Event]:
    return _fetch_venue(ORACLE_PARK_VENUE_ID)


def fetch_chase_center_events() -> list[Event]:
    return _fetch_venue(CHASE_CENTER_VENUE_ID)


def _fetch_venue(venue_id: str) -> list[Event]:
    api_key = os.environ.get("TICKETMASTER_API_KEY")
    if not api_key:
        raise RuntimeError("TICKETMASTER_API_KEY not set")
    events: list[Event] = []
    page = 0
    while True:
        response = requests.get(
            API_URL,
            params={
                "apikey": api_key,
                "venueId": venue_id,
                "size": PAGE_SIZE,
                "page": page,
                "sort": "date,asc",
            },
            headers=_HEADERS,
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        events.extend(parse_payload(payload))
        if page + 1 >= payload.get("page", {}).get("totalPages", 1):
            break
        page += 1
    return events


def parse_payload(payload: dict[str, Any]) -> list[Event]:
    events = []
    for raw in payload.get("_embedded", {}).get("events", []):
        if _should_skip(raw):
            continue
        event = _to_event(raw)
        if event is not None:
            events.append(event)
    return events


def _should_skip(raw: dict[str, Any]) -> bool:
    for c in raw.get("classifications") or []:
        sub = (c.get("subGenre") or {}).get("name")
        if sub in SKIP_SUBGENRES:
            return True
    return False


def _to_event(raw: dict[str, Any]) -> Event | None:
    """Build an Event, or None if the entry has no usable start date."""
    start = _start(raw.get("dates", {}).get("start") or {})
    if start is None:
        log.warning("skipping ticketmaster event %s: no usable start date", raw.get("id"))
        return None
    starts_at, time_tba = start
    venues = (raw.get("_embedded") or {}).get("venues") or []
    is_sports = any(
        ((c.get("segment") or {}).get("name") == "Sports") for c in raw.get("classifications") or []
    )
    return Event(
        source=SOURCE,
        source_id=raw["id"],
        name=raw["name"],
        starts_at=starts_at,
        venue=venues[0]["name"] if venues else "",
        category="sports" if is_sports else "concert",
        time_tba=time_tba,
    )


def _start(start: dict[str, Any]) -> tuple[datetime, bool] | None:
    """Resolve a start block to ``(starts_at, time_tba)``.

    Ticketmaster drops `dateTime` entirely when the start time hasn't been
    announced (`timeTBA`/`noSpecificTime`), leaving only `localDate`. Those
    events still fill the venue, so we keep them at midnight venue-local —
    enough to land them on the right day — and flag the time as unknown so
    nothing renders a fictitious clock time. An entry with neither field
    (`dateTBD`) can't be placed on any day at all, so it's dropped.
    """
    if dt := start.get("dateTime"):
        return datetime.fromisoformat(dt.replace("Z", "+00:00")), False
    if local_date := start.get("localDate"):
        midnight = datetime.fromisoformat(local_date).replace(tzinfo=VENUE_TZ)
        return midnight, True
    return None
