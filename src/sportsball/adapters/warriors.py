from datetime import datetime
from typing import Any

import requests

from sportsball.models import Event

SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
WARRIORS_TRICODE = "GSW"
SOURCE = "nba_cdn"
TIMEOUT_SECONDS = 30


def fetch_events() -> list[Event]:
    response = requests.get(SCHEDULE_URL, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return parse_payload(response.json())


def parse_payload(payload: dict[str, Any]) -> list[Event]:
    return [
        _game_to_event(game)
        for date_block in payload.get("leagueSchedule", {}).get("gameDates", [])
        for game in date_block.get("games", [])
        if _is_warriors_game(game)
    ]


def _is_warriors_game(game: dict[str, Any]) -> bool:
    return (
        game["homeTeam"]["teamTricode"] == WARRIORS_TRICODE
        or game["awayTeam"]["teamTricode"] == WARRIORS_TRICODE
    )


def _game_to_event(game: dict[str, Any]) -> Event:
    away = _team_full_name(game["awayTeam"])
    home = _team_full_name(game["homeTeam"])
    return Event(
        source=SOURCE,
        source_id=str(game["gameId"]),
        name=f"{away} at {home}",
        starts_at=datetime.fromisoformat(game["gameDateTimeUTC"].replace("Z", "+00:00")),
        venue=game.get("arenaName", ""),
    )


def _team_full_name(team: dict[str, Any]) -> str:
    return f"{team['teamCity']} {team['teamName']}"
