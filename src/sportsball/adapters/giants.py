from datetime import datetime
from typing import Any

import requests

from sportsball.models import Event

STATS_API_URL = "https://statsapi.mlb.com/api/v1/schedule"
GIANTS_TEAM_ID = 137
SOURCE = "mlb_statsapi"
TIMEOUT_SECONDS = 30


def fetch_events(season: int) -> list[Event]:
    response = requests.get(
        STATS_API_URL,
        params={
            "sportId": 1,
            "teamId": GIANTS_TEAM_ID,
            "season": season,
            "hydrate": "team,venue",
        },
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
    )
