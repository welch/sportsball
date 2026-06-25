from datetime import datetime
from typing import Any

import requests

from sportsball.models import Event

# We used to read cdn.nba.com's league-wide schedule JSON, but Akamai (the WAF
# fronting nba.com) escalated from header sniffing to blocking by source IP and
# started 403'ing App Engine's egress ranges — a block no header dressing can
# clear from a datacenter IP. ESPN's public schedule API serves the same data
# and isn't gated that way, so we read the Warriors' team schedule from it.
SCHEDULE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/gs/schedule"
SOURCE = "espn_nba"
TIMEOUT_SECONDS = 30

# ESPN splits a season into separate payloads by phase: 1=preseason,
# 2=regular season, 3=postseason. We fetch all three so a preseason or playoff
# game at Chase Center still flips the day, the way the old league feed did.
SEASON_TYPES = (1, 2, 3)


def nba_season_for(year: int, month: int) -> int:
    """ESPN's season label for a given calendar year/month.

    A season is labelled by the calendar year it *ends* in (2025-26 ->
    season=2026). The fall half (Oct-Dec) of a season therefore belongs to the
    next year's label; Jan-Sep belongs to the current year's. We split at July
    so that once the offseason ends we're already pointed at the upcoming
    season — ESPN simply returns no games until its schedule is published.
    """
    return year + 1 if month >= 7 else year


def fetch_events() -> list[Event]:
    now = datetime.now()
    season = nba_season_for(now.year, now.month)
    events: dict[str, Event] = {}
    for season_type in SEASON_TYPES:
        response = requests.get(
            SCHEDULE_URL,
            params={"season": season, "seasontype": season_type},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        for event in parse_payload(response.json()):
            # Dedupe by source_id in case a game surfaces under two phases.
            events[event.source_id] = event
    return list(events.values())


def parse_payload(payload: dict[str, Any]) -> list[Event]:
    return [_game_to_event(game) for game in payload.get("events", [])]


def _game_to_event(game: dict[str, Any]) -> Event:
    competition = (game.get("competitions") or [{}])[0]
    return Event(
        source=SOURCE,
        source_id=str(game["id"]),
        # ESPN's `name` is already "Away Team at Home Team".
        name=game["name"],
        starts_at=datetime.fromisoformat(game["date"].replace("Z", "+00:00")),
        venue=competition.get("venue", {}).get("fullName", ""),
    )
