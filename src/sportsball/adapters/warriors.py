from datetime import datetime
from typing import Any

import requests

from sportsball.models import Event

SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
WARRIORS_TRICODE = "GSW"
SOURCE = "nba_cdn"
TIMEOUT_SECONDS = 30

# Akamai (the WAF fronting cdn.nba.com) blocks requests that don't look like
# an nba.com browser XHR. The default `requests` User-Agent gets 403 Forbidden;
# adding Origin/Referer plus a Chrome-shaped UA and the sec-ch-* / sec-fetch-*
# signals nba.com itself sends gets us through.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}


def fetch_events() -> list[Event]:
    response = requests.get(SCHEDULE_URL, headers=_BROWSER_HEADERS, timeout=TIMEOUT_SECONDS)
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
