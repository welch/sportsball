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
from sportsball.stats import AdapterStats

BLOB_NAME = "events.json"

log = logging.getLogger(__name__)


def _bucket_name() -> str | None:
    return os.environ.get("EVENTS_BUCKET")


def _client() -> Any:
    # Imported lazily so module import doesn't require the dep at runtime
    # (and tests can run without GCS auth).
    from google.cloud import storage

    return storage.Client()


def write_events(
    events: list[Event],
    fetched_at: datetime,
    previously_unseen: list[Event] | None = None,
    adapter_stats: list[AdapterStats] | None = None,
) -> None:
    """Persist the cron's full snapshot to the configured bucket.

    ``previously_unseen`` is the subset of ``events`` that didn't appear in
    the previous cron's snapshot — what's "new" this run. ``adapter_stats``
    is the cron's per-adapter outcome summary; persisting it lets a future
    serving instance show the cron's view of adapter health on the health
    page even after that cron's instance has been scaled to zero.

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
        "previously_unseen": [e.model_dump(mode="json") for e in (previously_unseen or [])],
        "adapter_stats": [s.model_dump(mode="json") for s in (adapter_stats or [])],
    }
    body = json.dumps(payload, separators=(",", ":"))
    blob = _client().bucket(bucket).blob(BLOB_NAME)
    blob.cache_control = "no-cache"
    blob.upload_from_string(body, content_type="application/json")


def read_events() -> tuple[list[Event], datetime, list[Event], list[AdapterStats]] | None:
    """Read the snapshot back, or ``None`` if it can't be loaded.

    Returns ``(events, fetched_at, previously_unseen, adapter_stats)``. The
    final two elements default to ``[]`` when reading an older blob written
    before they existed.

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
        previously_unseen = [Event.model_validate(e) for e in payload.get("previously_unseen", [])]
        adapter_stats = [AdapterStats.model_validate(s) for s in payload.get("adapter_stats", [])]
    except Exception:
        log.exception("storage payload malformed")
        return None
    return events, fetched_at, previously_unseen, adapter_stats


def current_generation() -> int | None:
    """Generation number of the snapshot blob, without downloading its body.

    GCS bumps ``generation`` on every overwrite, so comparing it against the
    generation a serving instance loaded is a cheap "is my snapshot still
    the current one?" — a metadata GET rather than the whole payload.

    Returns ``None`` for every "can't tell" case: bucket unset, blob missing,
    transient API error. Callers read that as "no change" rather than "gone",
    so a GCS hiccup leaves the instance serving its cache instead of
    stampeding the upstream adapters.
    """
    bucket = _bucket_name()
    if not bucket:
        return None
    try:
        blob = _client().bucket(bucket).get_blob(BLOB_NAME)
    except Exception:
        log.exception("storage generation check failed")
        return None
    return blob.generation if blob is not None else None


def previously_unseen(new_events: list[Event], prev_events: list[Event]) -> list[Event]:
    """Subset of ``new_events`` whose ``(source, source_id)`` wasn't in ``prev_events``."""
    prev_keys = {(e.source, e.source_id) for e in prev_events}
    return [e for e in new_events if (e.source, e.source_id) not in prev_keys]
