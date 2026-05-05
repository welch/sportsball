import json
from pathlib import Path

from sportsball.adapters.warriors import parse_payload

FIXTURE = Path(__file__).parent / "fixtures" / "warriors_schedule_2025_26.json"


def _load() -> dict:
    return json.loads(FIXTURE.read_text())


def test_parse_returns_full_warriors_season() -> None:
    events = parse_payload(_load())
    assert len(events) == 89


def test_event_fields_for_first_game() -> None:
    events = parse_payload(_load())
    e = events[0]
    assert e.source == "nba_cdn"
    assert e.source_id == "0012500030"
    assert e.name == "Los Angeles Lakers at Golden State Warriors"
    assert e.venue == "Chase Center"
    assert e.starts_at.isoformat() == "2025-10-06T00:30:00+00:00"
    assert e.starts_at.tzinfo is not None


def test_home_vs_away_split_by_arena() -> None:
    events = parse_payload(_load())
    home = [e for e in events if e.venue == "Chase Center"]
    away = [e for e in events if e.venue != "Chase Center"]
    assert len(home) == 44
    assert len(away) == 45


def test_only_warriors_games_included() -> None:
    events = parse_payload(_load())
    for e in events:
        assert "Golden State Warriors" in e.name


def test_empty_payload_yields_no_events() -> None:
    assert parse_payload({}) == []
    assert parse_payload({"leagueSchedule": {"gameDates": []}}) == []
