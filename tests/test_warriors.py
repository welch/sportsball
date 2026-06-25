import json
from pathlib import Path

from sportsball.adapters.warriors import nba_season_for, parse_payload

REGULAR_FIXTURE = Path(__file__).parent / "fixtures" / "warriors_espn_2025_26_regular.json"
PRESEASON_FIXTURE = Path(__file__).parent / "fixtures" / "warriors_espn_2025_26_preseason.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def test_parse_returns_one_event_per_game() -> None:
    events = parse_payload(_load(REGULAR_FIXTURE))
    assert len(events) == 83


def test_event_fields_for_first_game() -> None:
    events = parse_payload(_load(REGULAR_FIXTURE))
    e = events[0]
    assert e.source == "espn_nba"
    assert e.source_id == "401809244"
    assert e.name == "Golden State Warriors at Los Angeles Lakers"
    assert e.venue == "crypto.com Arena"
    assert e.starts_at.isoformat() == "2025-10-22T02:00:00+00:00"
    assert e.starts_at.tzinfo is not None


def test_home_vs_away_split_by_arena() -> None:
    events = parse_payload(_load(REGULAR_FIXTURE))
    home = [e for e in events if e.venue == "Chase Center"]
    away = [e for e in events if e.venue != "Chase Center"]
    assert len(home) == 41
    assert len(away) == 42


def test_only_warriors_games_included() -> None:
    events = parse_payload(_load(REGULAR_FIXTURE))
    for e in events:
        assert "Golden State Warriors" in e.name


def test_preseason_games_are_parsed() -> None:
    # ESPN splits the season into preseason / regular / postseason payloads;
    # the adapter fetches all phases so preseason home games at Chase Center
    # still flip the day "fucked".
    events = parse_payload(_load(PRESEASON_FIXTURE))
    assert len(events) == 5


def test_empty_payload_yields_no_events() -> None:
    assert parse_payload({}) == []
    assert parse_payload({"events": []}) == []


def test_nba_season_straddles_the_new_year() -> None:
    # ESPN labels a season by the calendar year it ends in: the 2025-26
    # season is season=2026. The fall half of a season belongs to the
    # *next* year's label; Jan-Jun belongs to the current year's.
    assert nba_season_for(2026, 10) == 2027  # Oct 2026 -> 2026-27 season
    assert nba_season_for(2026, 12) == 2027
    assert nba_season_for(2026, 1) == 2026  # Jan 2026 -> 2025-26 season
    assert nba_season_for(2026, 6) == 2026  # offseason, last season's label
