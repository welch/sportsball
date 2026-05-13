import os
from datetime import datetime
from typing import Any

import requests

from sportsball.models import Event

API_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
SOURCE = "ticketmaster_discovery"

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
    return [
        _to_event(raw)
        for raw in payload.get("_embedded", {}).get("events", [])
        if not _should_skip(raw)
    ]


def _should_skip(raw: dict[str, Any]) -> bool:
    for c in raw.get("classifications") or []:
        sub = (c.get("subGenre") or {}).get("name")
        if sub in SKIP_SUBGENRES:
            return True
    return False


def _to_event(raw: dict[str, Any]) -> Event:
    venues = (raw.get("_embedded") or {}).get("venues") or []
    is_sports = any(
        ((c.get("segment") or {}).get("name") == "Sports") for c in raw.get("classifications") or []
    )
    return Event(
        source=SOURCE,
        source_id=raw["id"],
        name=raw["name"],
        starts_at=datetime.fromisoformat(raw["dates"]["start"]["dateTime"].replace("Z", "+00:00")),
        venue=venues[0]["name"] if venues else "",
        category="sports" if is_sports else "concert",
    )
