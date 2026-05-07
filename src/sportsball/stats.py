"""Lightweight in-process telemetry for the health endpoint.

Holds per-adapter outcomes (last success / last failure) and a rolling
24-hour deque of HTTP response status codes. Single-instance only — GAE
is configured for one instance, so a per-process structure is enough.
All access is serialized through one lock; everything is plain stdlib.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Iterable
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from sportsball.aggregator import PT

REQUEST_WINDOW = timedelta(hours=24)


class AdapterStats(BaseModel):
    """Snapshot of one adapter's most recent success and failure."""

    name: str
    last_success_at: datetime | None = None
    last_event_count: int | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None


class RequestSummary(BaseModel):
    """Rolling-window HTTP counters."""

    total: int = 0
    by_class: dict[str, int] = Field(default_factory=dict)


_lock = threading.Lock()
_adapters: dict[str, AdapterStats] = {}
_requests: deque[tuple[datetime, int]] = deque()


def _now() -> datetime:
    return datetime.now(tz=PT)


def record_adapter_success(name: str, event_count: int) -> None:
    """Mark `name` as having returned `event_count` events at this moment.

    Failure metadata is preserved alongside — it represents the most recent
    failure observed, regardless of whether a later success has occurred.
    The health template uses the timestamps to decide which is fresher.
    """
    with _lock:
        existing = _adapters.get(name)
        _adapters[name] = AdapterStats(
            name=name,
            last_success_at=_now(),
            last_event_count=event_count,
            last_failure_at=existing.last_failure_at if existing else None,
            last_error=existing.last_error if existing else None,
        )


def record_adapter_failure(name: str, message: str) -> None:
    """Mark `name` as having failed with `message` at this moment."""
    with _lock:
        existing = _adapters.get(name)
        _adapters[name] = AdapterStats(
            name=name,
            last_success_at=existing.last_success_at if existing else None,
            last_event_count=existing.last_event_count if existing else None,
            last_failure_at=_now(),
            last_error=message,
        )


def adapter_stats(names: Iterable[str] | None = None) -> list[AdapterStats]:
    """Snapshot of recorded adapter stats.

    If `names` is provided, returns one entry per requested name in that
    order, filling in empty AdapterStats for adapters with no recorded
    activity yet. Otherwise returns whatever has been recorded.
    """
    with _lock:
        if names is None:
            return [stats.model_copy() for stats in _adapters.values()]
        return [
            (_adapters[name].model_copy() if name in _adapters else AdapterStats(name=name))
            for name in names
        ]


def record_request(status_code: int) -> None:
    """Add an HTTP response to the 24-hour rolling deque."""
    now = _now()
    with _lock:
        _requests.append((now, status_code))
        _prune_locked(now)


def request_summary() -> RequestSummary:
    """Total + per-class counts over the rolling 24-hour window."""
    now = _now()
    with _lock:
        _prune_locked(now)
        by_class: dict[str, int] = {}
        for _, code in _requests:
            key = f"{code // 100}xx"
            by_class[key] = by_class.get(key, 0) + 1
        return RequestSummary(total=len(_requests), by_class=by_class)


def _prune_locked(now: datetime) -> None:
    """Drop entries older than REQUEST_WINDOW. Caller must hold _lock."""
    cutoff = now - REQUEST_WINDOW
    while _requests and _requests[0][0] < cutoff:
        _requests.popleft()


def snapshot_adapter_stats() -> list[AdapterStats]:
    """Return a copy of every recorded `AdapterStats` for persistence.

    Pairs with `load_adapter_stats` so the cron-run snapshot can travel
    through Cloud Storage and be re-instated on a fresh instance.
    """
    with _lock:
        return [s.model_copy() for s in _adapters.values()]


def load_adapter_stats(snapshot: list[AdapterStats]) -> None:
    """Replace recorded adapter stats with `snapshot`.

    The HTTP-request rolling deque is intentionally **not** touched —
    that's per-instance traffic data and shouldn't be borrowed from a
    different process.
    """
    with _lock:
        _adapters.clear()
        for s in snapshot:
            _adapters[s.name] = s.model_copy()


def reset() -> None:
    """Clear all recorded state. Test-only helper."""
    with _lock:
        _adapters.clear()
        _requests.clear()
