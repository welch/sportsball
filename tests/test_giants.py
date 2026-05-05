import json
from pathlib import Path

from sportsball.adapters.giants import parse_payload

FIXTURE = Path(__file__).parent / "fixtures" / "giants_schedule_2026_04.json"


def _load() -> dict:
    return json.loads(FIXTURE.read_text())


def test_parse_returns_one_event_per_game() -> None:
    payload = _load()
    events = parse_payload(payload)
    assert len(events) == payload["totalGames"] == 9


def test_event_fields_for_first_game() -> None:
    events = parse_payload(_load())
    e = events[0]
    assert e.source == "mlb_statsapi"
    assert e.source_id == "823321"
    assert e.name == "San Francisco Giants at San Diego Padres"
    assert e.venue == "Petco Park"
    assert e.starts_at.isoformat() == "2026-04-01T20:10:00+00:00"
    assert e.starts_at.tzinfo is not None


def test_home_vs_away_split_via_venue() -> None:
    events = parse_payload(_load())
    home = [e for e in events if e.venue == "Oracle Park"]
    away = [e for e in events if e.venue != "Oracle Park"]
    assert len(home) == 7
    assert len(away) == 2
    assert {e.venue for e in away} == {"Petco Park", "Oriole Park at Camden Yards"}


def test_empty_payload_yields_no_events() -> None:
    assert parse_payload({"dates": []}) == []
    assert parse_payload({}) == []
