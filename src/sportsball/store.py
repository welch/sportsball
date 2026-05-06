"""Cloud Storage read/write of the cron-refreshed events blob.

Cron writes the canonical (events, fetched_at) snapshot to one JSON blob
once a day; serving instances read it on cold start so they don't re-fetch
adapters individually. Designed so local dev and any environment without
``EVENTS_BUCKET`` set degrades gracefully — read returns ``None``, write
is a logged warning, and callers fall back to fetching adapters directly.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any

from sportsball.models import Event

BLOB_NAME = "events.json"

log = logging.getLogger(__name__)


def _bucket_name() -> str | None:
    return os.environ.get("EVENTS_BUCKET")


def _client() -> Any:
    # Imported lazily so module import doesn't require the dep at runtime
    # (and tests can run without GCS auth).
    from google.cloud import storage

    return storage.Client()


def write_events(events: list[Event], fetched_at: datetime) -> None:
    """Persist events + fetched_at to the configured bucket.

    No-ops (with a warning log) when ``EVENTS_BUCKET`` is unset. Lets cron
    runs in environments without storage configured silently skip the write.
    """
    bucket = _bucket_name()
    if not bucket:
        log.warning("EVENTS_BUCKET unset; skipping storage write")
        return
    payload = {
        "fetched_at": fetched_at.isoformat(),
        "events": [e.model_dump(mode="json") for e in events],
    }
    body = json.dumps(payload, separators=(",", ":"))
    blob = _client().bucket(bucket).blob(BLOB_NAME)
    blob.cache_control = "no-cache"
    blob.upload_from_string(body, content_type="application/json")


def read_events() -> tuple[list[Event], datetime] | None:
    """Read the snapshot back, or ``None`` if it can't be loaded.

    Returns ``None`` for any failure mode the caller treats the same way:
    bucket env unset, blob missing, malformed payload, transient API error.
    Callers fall back to direct adapter fetching.
    """
    bucket = _bucket_name()
    if not bucket:
        return None
    try:
        blob = _client().bucket(bucket).blob(BLOB_NAME)
        body = blob.download_as_bytes()
    except Exception:
        log.exception("storage read failed")
        return None
    try:
        payload = json.loads(body)
        events = [Event.model_validate(e) for e in payload["events"]]
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
    except Exception:
        log.exception("storage payload malformed")
        return None
    return events, fetched_at
