"""Lightweight per-process telemetry plus a Cloud Logging query.

Holds per-adapter outcomes (last success / last failure) in a small
in-process dict — populated by `aggregator.fetch_all` on cron, persisted
across instance lifecycles via Cloud Storage so any serving instance can
display the cron's adapter view.

HTTP request counts are read from Cloud Logging on demand (with a small
TTL cache) so they survive instance scale-to-zero. We don't keep an
in-process counter because it would be misleadingly per-instance.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from sportsball.aggregator import PT

log = logging.getLogger(__name__)

# Cache the Cloud Logging summary so reloading /health doesn't re-list
# thousands of entries on every render.
_REQUEST_SUMMARY_TTL_SECONDS = 300.0

# Hard bounds on the log scan. Cloud Logging has no server-side count, so
# the summary is computed by walking entries at roughly 2.75ms apiece —
# meaning the cost of rendering /health scaled with how much traffic the
# site got. In Aug 2026 a crawler flood took the window to 93k entries and
# the scan to ~182s, well past gunicorn's 30s timeout, so the worker was
# killed and the page 500'd instead of degrading.
#
# TODO: replace the scan with Cloud Monitoring's
# `appengine.googleapis.com/http/server/response_count`, which carries a
# `response_code_class` label and aggregates server-side — exact counts in
# one call instead of a bounded sample that under-reports precisely when
# traffic spikes. Needs monitoring.googleapis.com enabled and
# roles/monitoring.viewer on the App Engine service account, which has only
# roles/logging.viewer today. Keep this scan as the fallback.
#
# The deadline is the real budget — it's the one that bounds page latency
# whatever the API is doing, and it's set well under gunicorn's 30s timeout
# so the request finishes on our terms rather than SIGABRT's. The entry cap
# is a backstop for the opposite case: entries arriving fast enough that we'd
# otherwise buffer an unreasonable number of them inside the budget.
_REQUEST_SCAN_DEADLINE_SECONDS = 5.0
_REQUEST_SCAN_MAX_ENTRIES = 20_000


class AdapterStats(BaseModel):
    """Snapshot of one adapter's most recent success and failure."""

    name: str
    last_success_at: datetime | None = None
    last_event_count: int | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None


class RequestSummary(BaseModel):
    """HTTP-request counters over a rolling window."""

    total: int = 0
    by_class: dict[str, int] = Field(default_factory=dict)
    # Exact status codes, when the source can supply them. Cloud Monitoring
    # labels each series with `response_code`, so 404 and 405 arrive
    # distinguishable; the log scan only ever populated classes. Empty when
    # the fallback produced the summary.
    by_code: dict[str, int] = Field(default_factory=dict)
    # Real page views: 2xx only. Excludes redirects (legacy-URL bot probes
    # bounce off the canonical 301 with no follow-up) and 4xx (mostly bots
    # scanning for /wp-admin/etc.). Vanity metric — the actual "how many
    # humans loaded my page" number.
    user_traffic: int = 0
    window_hours: int = 24
    # "cloud-monitoring" normally; "cloud-logging" when the metric query
    # failed and the bounded scan stood in; "unavailable" when both did.
    source: str = "cloud-monitoring"
    # True when the scan hit `_REQUEST_SCAN_MAX_ENTRIES` or its deadline and
    # stopped early. The counts are then floors, not totals — the template
    # renders them with a "+" so nobody reads a bounded scan as the real
    # number. Under normal traffic this is always False.
    truncated: bool = False


_lock = threading.Lock()
_adapters: dict[str, AdapterStats] = {}
_summary_cache: tuple[float, RequestSummary] | None = None


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


def snapshot_adapter_stats() -> list[AdapterStats]:
    """Return a copy of every recorded `AdapterStats` for persistence.

    Pairs with `load_adapter_stats` so the cron-run snapshot can travel
    through Cloud Storage and be re-instated on a fresh instance.
    """
    with _lock:
        return [s.model_copy() for s in _adapters.values()]


def load_adapter_stats(snapshot: list[AdapterStats]) -> None:
    """Replace recorded adapter stats with `snapshot`."""
    with _lock:
        _adapters.clear()
        for s in snapshot:
            _adapters[s.name] = s.model_copy()


def _logging_client() -> Any:
    # Defer import so module load doesn't require GCP auth (tests patch this).
    import os

    from google.cloud import logging as cloud_logging

    # GAE Standard sets GOOGLE_CLOUD_PROJECT automatically. ADC for a user
    # account doesn't carry a default project, so without this hint local
    # dev would 500 with "Project was not passed and could not be
    # determined from the environment." Falling back to bare Client() lets
    # workload-identity environments still auto-detect.
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project:
        return cloud_logging.Client(project=project)
    return cloud_logging.Client()


def _monitoring_client() -> Any:
    # Deferred like the logging client, for the same reason: importing must
    # not require GCP auth, and tests patch this.
    from google.cloud import monitoring_v3

    return monitoring_v3.MetricServiceClient()


# The App Engine metric carrying one counter per HTTP response. Its labels are
# `response_code` (the exact status) and `loading` (whether the request paid a
# cold start). Note it is NOT `response_code_class`, which is what the metric
# on the *Cloud Run* equivalent is called — grouping by that here silently
# yields one unlabelled series.
_RESPONSE_COUNT_METRIC = "appengine.googleapis.com/http/server/response_count"


def _query_request_summary_monitoring(window_hours: int = 24) -> RequestSummary:
    """Exact counts from Cloud Monitoring, aggregated server-side.

    One request, one point per status code, no relationship between the cost
    of this call and how much traffic there was — which is the whole point.
    The log scan it replaces walked entries at ~2.75ms apiece, so a day of
    crawler traffic put the real answer far outside any budget the page could
    afford; measured on 2026-08-19, 6,017 entries took 23.5s to count.

    Unlike the scan this cannot filter by URL — the metric carries no path
    label — so operator endpoints are included. Measured over the same window,
    they accounted for 0 of 6,017 requests, so the filter was excluding
    nothing and its loss costs no fidelity.
    """
    import os

    from google.cloud import monitoring_v3

    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT unset; cannot query monitoring")

    now = datetime.now(UTC)
    interval = monitoring_v3.TimeInterval(
        end_time=now, start_time=now - timedelta(hours=window_hours)
    )
    # One alignment bucket spanning the whole window collapses each series to a
    # single point, so the reducer hands back exactly one number per status.
    aggregation = monitoring_v3.Aggregation(
        alignment_period={"seconds": window_hours * 3600},
        per_series_aligner=monitoring_v3.Aggregation.Aligner.ALIGN_SUM,
        cross_series_reducer=monitoring_v3.Aggregation.Reducer.REDUCE_SUM,
        group_by_fields=["metric.labels.response_code"],
    )
    series = _monitoring_client().list_time_series(
        request={
            "name": f"projects/{project}",
            "filter": f'metric.type="{_RESPONSE_COUNT_METRIC}"',
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            "aggregation": aggregation,
        }
    )

    by_code: dict[str, int] = {}
    for ts in series:
        code = ts.metric.labels.get("response_code")
        if not code:
            continue
        by_code[code] = by_code.get(code, 0) + sum(int(p.value.int64_value) for p in ts.points)

    by_class: dict[str, int] = {}
    for code, count in by_code.items():
        by_class[f"{code[0]}xx"] = by_class.get(f"{code[0]}xx", 0) + count

    return RequestSummary(
        total=sum(by_code.values()),
        by_class=by_class,
        by_code=dict(sorted(by_code.items())),
        user_traffic=by_class.get("2xx", 0),
        window_hours=window_hours,
        source="cloud-monitoring",
        truncated=False,
    )


def _query_request_summary(window_hours: int = 24) -> RequestSummary:
    """Single-shot Cloud Logging query — no caching, bounded work.

    Stops at `_REQUEST_SCAN_MAX_ENTRIES` or `_REQUEST_SCAN_DEADLINE_SECONDS`,
    whichever comes first, and flags the result `truncated` when it does.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    cutoff_iso = cutoff.replace(microsecond=0, tzinfo=None).isoformat() + "Z"
    # Exclude operator/health/cron paths so the count reflects user traffic only.
    filter_ = (
        'resource.type="gae_app" '
        f'timestamp>="{cutoff_iso}" '
        "httpRequest.status>0 "
        'NOT httpRequest.requestUrl=~"^https?://[^/]+/health/" '
        'NOT httpRequest.requestUrl=~"/healthz$" '
        'NOT httpRequest.requestUrl=~"/tasks/refresh$"'
    )
    by_class: dict[str, int] = {}
    total = 0
    user_traffic = 0
    truncated = False
    scanned = 0
    deadline = time.monotonic() + _REQUEST_SCAN_DEADLINE_SECONDS
    for entry in _logging_client().list_entries(filter_=filter_, page_size=1000):
        # Checked per entry rather than per page: a page is 1000 entries and
        # ~2.75s of parsing, which is most of the budget on its own.
        scanned += 1
        if scanned > _REQUEST_SCAN_MAX_ENTRIES or time.monotonic() > deadline:
            truncated = True
            break
        http = getattr(entry, "http_request", None)
        if not http:
            continue
        status = http.get("status") if isinstance(http, dict) else getattr(http, "status", 0)
        if not status:
            continue
        cls = f"{status // 100}xx"
        by_class[cls] = by_class.get(cls, 0) + 1
        total += 1
        if 200 <= status < 300:
            user_traffic += 1
    if truncated:
        log.warning(
            "request summary scan truncated at %d entries; counts are floors",
            total,
        )
    return RequestSummary(
        total=total,
        by_class=by_class,
        user_traffic=user_traffic,
        window_hours=window_hours,
        source="cloud-logging",
        truncated=truncated,
    )


def request_summary(window_hours: int = 24) -> RequestSummary:
    """Recent HTTP traffic, cached in-process for ~5 min.

    Cloud Monitoring first: exact, server-side aggregated, and costing the
    same whether the site saw a hundred requests or a hundred thousand. The
    bounded log scan stands in when that fails — it under-reports during a
    traffic spike, which is when the page matters most, but a floor with a
    warning beats no number at all.

    Returns an empty summary with `source="unavailable"` only if both fail,
    so the health page degrades rather than 500ing.
    """
    global _summary_cache
    now = time.time()
    with _lock:
        if _summary_cache is not None and now - _summary_cache[0] < _REQUEST_SUMMARY_TTL_SECONDS:
            return _summary_cache[1]

    summary: RequestSummary | None = None
    try:
        summary = _query_request_summary_monitoring(window_hours=window_hours)
    except Exception:
        # Expected while `roles/monitoring.viewer` is missing, and whenever the
        # API is unreachable. Logged rather than raised so the scan can try.
        log.exception("cloud-monitoring request summary query failed; falling back")
        try:
            summary = _query_request_summary(window_hours=window_hours)
        except Exception:
            log.exception("cloud-logging request summary query failed too")
            return RequestSummary(window_hours=window_hours, source="unavailable")

    with _lock:
        _summary_cache = (now, summary)
    return summary


def reset() -> None:
    """Clear all recorded state. Test-only helper."""
    global _summary_cache
    with _lock:
        _adapters.clear()
        _summary_cache = None
