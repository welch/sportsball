import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from sportsball import store
from sportsball.aggregator import PT
from sportsball.models import Event


def _ev(
    sid: str,
    *,
    venue: str = "Oracle Park",
    starts_at: datetime | None = None,
    name: str | None = None,
) -> Event:
    return Event(
        source="test",
        source_id=sid,
        name=name or f"event {sid}",
        starts_at=starts_at or datetime(2026, 5, 15, 19, 0, tzinfo=PT),
        venue=venue,
        kind="home",
    )


def test_write_skipped_when_bucket_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVENTS_BUCKET", raising=False)
    # Should not raise even though no GCS client configured.
    store.write_events([_ev("1")], datetime.now(tz=PT))


def test_read_returns_none_when_bucket_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVENTS_BUCKET", raising=False)
    assert store.read_events() is None


def test_write_serializes_via_pydantic_and_uploads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVENTS_BUCKET", "test-bucket")
    fake_blob = MagicMock()
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    monkeypatch.setattr(store, "_client", lambda: fake_client)

    fetched_at = datetime(2026, 5, 6, 6, 0, tzinfo=PT)
    store.write_events([_ev("1"), _ev("2", venue="Chase Center")], fetched_at)

    fake_client.bucket.assert_called_once_with("test-bucket")
    fake_bucket.blob.assert_called_once_with(store.BLOB_NAME)
    fake_blob.upload_from_string.assert_called_once()
    body, _kwargs = fake_blob.upload_from_string.call_args
    payload = json.loads(body[0])
    assert payload["fetched_at"] == fetched_at.isoformat()
    assert len(payload["events"]) == 2
    assert payload["events"][0]["source_id"] == "1"


def test_read_round_trips_with_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVENTS_BUCKET", "test-bucket")
    events = [_ev("1"), _ev("2", venue="Chase Center")]
    new_only = [_ev("2", venue="Chase Center")]
    fetched_at = datetime(2026, 5, 6, 6, 0, tzinfo=PT)
    payload = json.dumps(
        {
            "fetched_at": fetched_at.isoformat(),
            "events": [e.model_dump(mode="json") for e in events],
            "previously_unseen": [e.model_dump(mode="json") for e in new_only],
        }
    ).encode()

    fake_blob = MagicMock()
    fake_blob.download_as_bytes.return_value = payload
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    monkeypatch.setattr(store, "_client", lambda: fake_client)

    result = store.read_events()
    assert result is not None
    got_events, got_fetched_at, got_new, got_stats = result
    assert {e.source_id for e in got_events} == {"1", "2"}
    assert got_fetched_at == fetched_at
    assert [e.source_id for e in got_new] == ["2"]
    assert got_stats == []


def test_read_carries_adapter_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    from sportsball.stats import AdapterStats

    monkeypatch.setenv("EVENTS_BUCKET", "test-bucket")
    fetched_at = datetime(2026, 5, 7, 6, 0, tzinfo=PT)
    stats_in = [
        AdapterStats(name="giants.fetch_events", last_success_at=fetched_at, last_event_count=42),
        AdapterStats(
            name="warriors.fetch_events",
            last_failure_at=fetched_at,
            last_error="upstream 503",
        ),
    ]
    payload = json.dumps(
        {
            "fetched_at": fetched_at.isoformat(),
            "events": [],
            "previously_unseen": [],
            "adapter_stats": [s.model_dump(mode="json") for s in stats_in],
        }
    ).encode()

    fake_blob = MagicMock()
    fake_blob.download_as_bytes.return_value = payload
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    monkeypatch.setattr(store, "_client", lambda: fake_client)

    result = store.read_events()
    assert result is not None
    _events, _fetched, _new, got_stats = result
    by_name = {s.name: s for s in got_stats}
    assert by_name["giants.fetch_events"].last_event_count == 42
    assert by_name["warriors.fetch_events"].last_error == "upstream 503"


def test_read_handles_legacy_blob_without_diff_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old blobs → previously_unseen=[] and adapter_stats=[]."""
    monkeypatch.setenv("EVENTS_BUCKET", "test-bucket")
    events = [_ev("1")]
    fetched_at = datetime(2026, 5, 6, 6, 0, tzinfo=PT)
    payload = json.dumps(
        {
            "fetched_at": fetched_at.isoformat(),
            "events": [e.model_dump(mode="json") for e in events],
        }
    ).encode()

    fake_blob = MagicMock()
    fake_blob.download_as_bytes.return_value = payload
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    monkeypatch.setattr(store, "_client", lambda: fake_client)

    result = store.read_events()
    assert result is not None
    _events, _fetched, got_new, got_stats = result
    assert got_new == []
    assert got_stats == []


def test_previously_unseen_diffs_by_source_and_id() -> None:
    prev = [_ev("1"), _ev("2", venue="Chase Center")]
    new = [_ev("2", venue="Chase Center"), _ev("3")]
    diff = store.previously_unseen(new, prev)
    assert [e.source_id for e in diff] == ["3"]


def test_previously_unseen_first_run_returns_all() -> None:
    new = [_ev("1"), _ev("2")]
    diff = store.previously_unseen(new, [])
    assert {e.source_id for e in diff} == {"1", "2"}


# --- Retaining what has already happened ------------------------------------
#
# Cron replaces the snapshot with whatever the adapters just returned, so an
# event vanishes the moment its source stops listing it. Ticketmaster's
# Discovery API drops events once they are over — a past window returns zero
# results where an upcoming one returns dozens — which quietly erased every
# concert the day after it happened. MLB publishes a whole season, so Giants
# games survived and the loss was invisible on the calendar.
#
# A source dropping a *future* event still means something (a cancellation, a
# reschedule) and should still remove it. A source dropping a past one means
# only that the source has moved on.

NOW = datetime(2026, 8, 19, 6, 0, tzinfo=PT)


def test_past_events_survive_disappearing_from_their_source() -> None:
    happened = _ev("gone", starts_at=datetime(2026, 8, 1, 19, 0, tzinfo=PT))
    upcoming = _ev("keep", starts_at=datetime(2026, 9, 1, 19, 0, tzinfo=PT))
    merged = store.retain_occurred([upcoming], [happened, upcoming], NOW)
    assert {e.source_id for e in merged} == {"gone", "keep"}


def test_future_events_that_disappear_are_still_dropped() -> None:
    """A cancellation has to be able to take an event off the calendar."""
    cancelled = _ev("cancelled", starts_at=datetime(2026, 9, 1, 19, 0, tzinfo=PT))
    merged = store.retain_occurred([], [cancelled], NOW)
    assert merged == []


def test_the_fresh_copy_wins_over_the_retained_one() -> None:
    """Same event, still reported: take the source's current version, once."""
    old = _ev("1", starts_at=datetime(2026, 8, 1, 19, 0, tzinfo=PT), name="old name")
    new = _ev("1", starts_at=datetime(2026, 8, 1, 19, 0, tzinfo=PT), name="new name")
    merged = store.retain_occurred([new], [old], NOW)
    assert [e.name for e in merged] == ["new name"]


def test_retention_stops_at_the_browsable_horizon() -> None:
    """Nothing is kept that no visitor could navigate to; the blob is rewritten
    daily and would otherwise grow without bound."""
    ancient = _ev("ancient", starts_at=datetime(2024, 5, 1, 19, 0, tzinfo=PT))
    recent = _ev("recent", starts_at=datetime(2026, 8, 1, 19, 0, tzinfo=PT))
    merged = store.retain_occurred([], [ancient, recent], NOW)
    assert {e.source_id for e in merged} == {"recent"}


def test_an_event_earlier_today_counts_as_having_happened() -> None:
    """The cron runs at 06:00; something at 19:00 tonight is still upcoming,
    and last night's game is not."""
    tonight = _ev("tonight", starts_at=datetime(2026, 8, 19, 19, 0, tzinfo=PT))
    last_night = _ev("last-night", starts_at=datetime(2026, 8, 18, 19, 0, tzinfo=PT))
    merged = store.retain_occurred([], [tonight, last_night], NOW)
    assert {e.source_id for e in merged} == {"last-night"}


def test_retention_is_a_no_op_on_the_first_run() -> None:
    fresh = [_ev("1"), _ev("2")]
    assert store.retain_occurred(fresh, [], NOW) == fresh


def test_merged_events_come_back_in_date_order() -> None:
    a = _ev("a", starts_at=datetime(2026, 8, 1, 19, 0, tzinfo=PT))
    b = _ev("b", starts_at=datetime(2026, 8, 10, 19, 0, tzinfo=PT))
    c = _ev("c", starts_at=datetime(2026, 9, 5, 19, 0, tzinfo=PT))
    merged = store.retain_occurred([c], [a, b], NOW)
    assert [e.source_id for e in merged] == ["a", "b", "c"]


def test_read_returns_none_on_blob_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVENTS_BUCKET", "test-bucket")
    fake_blob = MagicMock()
    fake_blob.download_as_bytes.side_effect = RuntimeError("404 not found")
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    monkeypatch.setattr(store, "_client", lambda: fake_client)

    assert store.read_events() is None


def test_read_returns_none_on_malformed_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVENTS_BUCKET", "test-bucket")
    fake_blob = MagicMock()
    fake_blob.download_as_bytes.return_value = b"not json"
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    monkeypatch.setattr(store, "_client", lambda: fake_client)

    assert store.read_events() is None


def test_legacy_snapshot_without_kind_still_loads() -> None:
    """Blobs written before `kind` replaced `category` must still validate.

    Cron rewrites the snapshot daily, so this only covers the window between
    a deploy and the next refresh — but without it `read_events` would reject
    the entire payload and every cold start would hit the adapters directly.
    """
    legacy = {
        "source": "mlb_statsapi",
        "source_id": "1",
        "name": "Mets at Giants",
        "starts_at": "2026-05-05T02:05:00+00:00",
        "venue": "Oracle Park",
        "category": "sports",
    }
    assert Event.model_validate(legacy).kind == "home"
    # A Ticketmaster row can't be proven to be a home team, so it lands on
    # the safe side and one cron run corrects any Valkyries games.
    assert Event.model_validate({**legacy, "source": "ticketmaster_discovery"}).kind == "event"


def test_current_snapshot_kind_is_not_overwritten_by_the_bridge() -> None:
    current = {
        "source": "ticketmaster_discovery",
        "source_id": "2",
        "name": "Valkyries vs whoever",
        "starts_at": "2026-05-05T02:05:00+00:00",
        "venue": "Chase Center",
        "kind": "home",
    }
    assert Event.model_validate(current).kind == "home"


def test_current_generation_none_when_bucket_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVENTS_BUCKET", raising=False)
    assert store.current_generation() is None


def test_current_generation_reads_metadata_not_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point is that this is cheap — metadata only, no download."""
    monkeypatch.setenv("EVENTS_BUCKET", "test-bucket")
    fake_blob = MagicMock()
    fake_blob.generation = 1748301234567890
    fake_bucket = MagicMock()
    fake_bucket.get_blob.return_value = fake_blob
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    monkeypatch.setattr(store, "_client", lambda: fake_client)

    assert store.current_generation() == 1748301234567890
    fake_bucket.get_blob.assert_called_once_with(store.BLOB_NAME)
    fake_blob.download_as_bytes.assert_not_called()


def test_current_generation_none_when_blob_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVENTS_BUCKET", "test-bucket")
    fake_bucket = MagicMock()
    fake_bucket.get_blob.return_value = None
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    monkeypatch.setattr(store, "_client", lambda: fake_client)

    assert store.current_generation() is None


def test_current_generation_swallows_api_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GCS hiccup must read as "no change", not as "the blob is gone" —
    callers would otherwise fall back to hammering the adapters."""
    monkeypatch.setenv("EVENTS_BUCKET", "test-bucket")
    fake_client = MagicMock()
    fake_client.bucket.side_effect = RuntimeError("503 backend error")
    monkeypatch.setattr(store, "_client", lambda: fake_client)

    assert store.current_generation() is None
