import json
from datetime import date
from pathlib import Path

from sportsball.adapters.ticketmaster import parse_payload
from sportsball.aggregator import PT

FIXTURES = Path(__file__).parent / "fixtures"
ORACLE = FIXTURES / "ticketmaster_oracle_park.json"
CHASE = FIXTURES / "ticketmaster_chase_center.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def test_oracle_park_filters_out_mlb() -> None:
    events = parse_payload(_load(ORACLE))
    # Captured fixture: 66 total, 64 MLB (filtered) → 2 concerts remain.
    assert len(events) == 2
    assert all(e.venue == "Oracle Park" for e in events)
    assert all("Giants" not in e.name for e in events)


def test_chase_center_keeps_wnba_and_concerts() -> None:
    events = parse_payload(_load(CHASE))
    # 47 total, 0 NBA in current fixture → all 47 pass.
    assert len(events) == 47
    assert all(e.venue == "Chase Center" for e in events)
    # Verify Valkyries (WNBA) survived the NBA filter.
    valkyries = [e for e in events if "Valkyries" in e.name]
    assert len(valkyries) >= 5
    # And at least one music event.
    assert any("Florence" in e.name or "Demi Lovato" in e.name for e in events)


def test_event_fields_well_formed() -> None:
    events = parse_payload(_load(CHASE))
    e = events[0]
    assert e.source == "ticketmaster_discovery"
    assert e.source_id  # non-empty
    assert e.name
    assert e.starts_at.tzinfo is not None
    assert e.venue == "Chase Center"
    assert e.category in ("sports", "concert")


def test_category_derived_from_segment() -> None:
    payload = {
        "_embedded": {
            "events": [
                {
                    "id": "concert",
                    "name": "Music event",
                    "dates": {"start": {"dateTime": "2026-05-15T19:00:00Z"}},
                    "classifications": [
                        {"segment": {"name": "Music"}, "subGenre": {"name": "Pop"}}
                    ],
                    "_embedded": {"venues": [{"name": "Chase Center"}]},
                },
                {
                    "id": "wnba",
                    "name": "Valkyries vs whoever",
                    "dates": {"start": {"dateTime": "2026-05-16T19:00:00Z"}},
                    "classifications": [
                        {"segment": {"name": "Sports"}, "subGenre": {"name": "WNBA"}}
                    ],
                    "_embedded": {"venues": [{"name": "Chase Center"}]},
                },
                {
                    "id": "racing",
                    "name": "Hot Wheels Monster Trucks",
                    "dates": {"start": {"dateTime": "2026-05-17T19:00:00Z"}},
                    "classifications": [
                        {
                            "segment": {"name": "Sports"},
                            "subGenre": {"name": "Motorsports/Racing"},
                        }
                    ],
                    "_embedded": {"venues": [{"name": "Chase Center"}]},
                },
            ]
        }
    }
    events = parse_payload(payload)
    by_id = {e.source_id: e.category for e in events}
    assert by_id["concert"] == "concert"
    assert by_id["wnba"] == "sports"
    assert by_id["racing"] == "sports"


def test_chase_fixture_has_mix_of_categories() -> None:
    events = parse_payload(_load(CHASE))
    cats = {e.category for e in events}
    assert "sports" in cats  # Valkyries
    assert "concert" in cats  # Music events


def test_filter_drops_explicit_subgenre() -> None:
    payload = {
        "_embedded": {
            "events": [
                {
                    "id": "1",
                    "name": "Should drop",
                    "dates": {"start": {"dateTime": "2026-05-15T19:00:00Z"}},
                    "classifications": [{"subGenre": {"name": "MLB"}}],
                    "_embedded": {"venues": [{"name": "Oracle Park"}]},
                },
                {
                    "id": "2",
                    "name": "Should drop too",
                    "dates": {"start": {"dateTime": "2026-05-16T19:00:00Z"}},
                    "classifications": [{"subGenre": {"name": "NBA"}}],
                    "_embedded": {"venues": [{"name": "Chase Center"}]},
                },
                {
                    "id": "3",
                    "name": "Keeper",
                    "dates": {"start": {"dateTime": "2026-05-17T19:00:00Z"}},
                    "classifications": [{"subGenre": {"name": "WNBA"}}],
                    "_embedded": {"venues": [{"name": "Chase Center"}]},
                },
                {
                    "id": "4",
                    "name": "Concert keeper",
                    "dates": {"start": {"dateTime": "2026-05-18T19:00:00Z"}},
                    "classifications": [{"subGenre": {"name": "Pop"}}],
                    "_embedded": {"venues": [{"name": "Chase Center"}]},
                },
            ]
        }
    }
    events = parse_payload(payload)
    assert {e.source_id for e in events} == {"3", "4"}


def test_empty_payload() -> None:
    assert parse_payload({}) == []
    assert parse_payload({"_embedded": {"events": []}}) == []


def test_time_tba_event_falls_back_to_local_date() -> None:
    """Ticketmaster omits `dateTime` when the start time isn't announced.

    The event still has a known local date and still fills the venue, so it
    has to survive parsing — one of these took the whole Chase Center
    adapter down with a KeyError.
    """
    payload = {
        "_embedded": {
            "events": [
                {
                    "id": "tba",
                    "name": "Womens Classic",
                    "dates": {
                        "start": {
                            "localDate": "2026-12-20",
                            "dateTBD": False,
                            "dateTBA": False,
                            "timeTBA": True,
                            "noSpecificTime": True,
                        }
                    },
                    "classifications": [
                        {"segment": {"name": "Sports"}, "subGenre": {"name": "NCAA Basketball"}}
                    ],
                    "_embedded": {"venues": [{"name": "Chase Center"}]},
                },
            ]
        }
    }
    events = parse_payload(payload)
    assert len(events) == 1
    e = events[0]
    assert e.time_tba is True
    # Midnight PT on the announced local date, so day-grouping puts it on
    # 2026-12-20 rather than sliding into the 19th or 21st.
    local = e.starts_at.astimezone(PT)
    assert local.date() == date(2026, 12, 20)
    assert (local.hour, local.minute) == (0, 0)


def test_dated_event_is_not_flagged_tba() -> None:
    payload = {
        "_embedded": {
            "events": [
                {
                    "id": "dated",
                    "name": "Has a time",
                    "dates": {"start": {"dateTime": "2026-05-15T19:00:00Z"}},
                    "classifications": [{"subGenre": {"name": "Pop"}}],
                    "_embedded": {"venues": [{"name": "Chase Center"}]},
                },
            ]
        }
    }
    (e,) = parse_payload(payload)
    assert e.time_tba is False


def test_undated_event_is_dropped() -> None:
    """No dateTime and no localDate — nothing to hang a day on, so skip it
    rather than invent one."""
    payload = {
        "_embedded": {
            "events": [
                {
                    "id": "no-date",
                    "name": "Someday",
                    "dates": {"start": {"dateTBD": True}},
                    "classifications": [{"subGenre": {"name": "Pop"}}],
                    "_embedded": {"venues": [{"name": "Chase Center"}]},
                },
                {
                    "id": "keeper",
                    "name": "Real event",
                    "dates": {"start": {"dateTime": "2026-05-15T19:00:00Z"}},
                    "classifications": [{"subGenre": {"name": "Pop"}}],
                    "_embedded": {"venues": [{"name": "Chase Center"}]},
                },
            ]
        }
    }
    events = parse_payload(payload)
    assert {e.source_id for e in events} == {"keeper"}
