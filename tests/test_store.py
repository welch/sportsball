import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from sportsball import store
from sportsball.aggregator import PT
from sportsball.models import Event


def _ev(sid: str, *, venue: str = "Oracle Park") -> Event:
    return Event(
        source="test",
        source_id=sid,
        name=f"event {sid}",
        starts_at=datetime(2026, 5, 15, 19, 0, tzinfo=PT),
        venue=venue,
        category="sports",
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


def test_read_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVENTS_BUCKET", "test-bucket")
    events = [_ev("1"), _ev("2", venue="Chase Center")]
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
    got_events, got_fetched_at = result
    assert {e.source_id for e in got_events} == {"1", "2"}
    assert got_fetched_at == fetched_at


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
