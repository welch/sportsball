from datetime import datetime
from typing import Any

import requests

from sportsball.models import Event

STATS_API_URL = "https://statsapi.mlb.com/api/v1/schedule"
GIANTS_TEAM_ID = 137
SOURCE = "mlb_statsapi"
TIMEOUT_SECONDS = 30

# statsapi.mlb.com is fronted by Akamai. Defensive: send the headers mlb.com
# itself sends, so an Akamai Bot Manager rule update doesn't silently 403 us
# the way one did to the NBA CDN.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.mlb.com",
    "Referer": "https://www.mlb.com/",
}


def fetch_events(season: int) -> list[Event]:
    response = requests.get(
        STATS_API_URL,
        params={
            "sportId": 1,
            "teamId": GIANTS_TEAM_ID,
            "season": season,
            "hydrate": "team,venue",
        },
        headers=_BROWSER_HEADERS,
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return parse_payload(response.json())


def parse_payload(payload: dict[str, Any]) -> list[Event]:
    return [
        _game_to_event(game)
        for date_block in payload.get("dates", [])
        for game in date_block.get("games", [])
    ]


def _game_to_event(game: dict[str, Any]) -> Event:
    away = game["teams"]["away"]["team"]["name"]
    home = game["teams"]["home"]["team"]["name"]
    return Event(
        source=SOURCE,
        source_id=str(game["gamePk"]),
        name=f"{away} at {home}",
        starts_at=datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00")),
        venue=game.get("venue", {}).get("name", ""),
        # This adapter only ever returns Giants games, home and away. The
        # away ones get filtered out by venue downstream.
        kind="home",
    )
