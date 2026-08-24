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

# The two windows the health page shows side by side. The day is the trend;
# the half hour is for reading the page after an alert email lands, since
# observed surges run about fifteen minutes and a day-wide count buries them.
DEFAULT_WINDOW_MINUTES = 24 * 60
RECENT_WINDOW_MINUTES = 30

# Cache the summary so reloading /health doesn't re-query on every render.
# The short window gets a shorter TTL: five minutes of staleness is a sixth
# of a day but a sixth of a half hour, and the whole point of that column is
# that it moves. Monitoring costs the same either way.
_REQUEST_SUMMARY_TTL_SECONDS = 300.0
_RECENT_SUMMARY_TTL_SECONDS = 60.0
_SHORT_WINDOW_MINUTES = 60

# Hard bounds on the log scan. Cloud Logging has no server-side count, so
# the summary is computed by walking entries at roughly 2.75ms apiece —
# meaning the cost of rendering /health scaled with how much traffic the
# site got. In Aug 2026 a crawler flood took the window to 93k entries and
# the scan to ~182s, well past gunicorn's 30s timeout, so the worker was
# killed and the page 500'd instead of degrading.
#
# Cloud Monitoring replaced the scan as the primary source in v0.10.0; this
# stays as the fallback for when the metric query fails.
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
    window_minutes: int = DEFAULT_WINDOW_MINUTES
    # "cloud-monitoring" normally; "cloud-logging" when the metric query
    # failed and the bounded scan stood in; "unavailable" when both did.
    source: str = "cloud-monitoring"
    # True when the scan hit `_REQUEST_SCAN_MAX_ENTRIES` or its deadline and
    # stopped early. The counts are then floors, not totals — the template
    # renders them with a "+" so nobody reads a bounded scan as the real
    # number. Under normal traffic this is always False.
    truncated: bool = False

    @property
    def window_label(self) -> str:
        """Human phrasing of the window, for headings and column titles."""
        if self.window_minutes % 60:
            return f"{self.window_minutes} min"
        hours = self.window_minutes // 60
        return "24 hours" if hours == 24 else f"{hours}h"

    def counts(self) -> dict[str, int]:
        """Per-status counts, keyed by exact code when the source had them."""
        return self.by_code or self.by_class


class TrafficRow(BaseModel):
    """One line of the health page's request table, both windows side by side.

    Built here rather than in Jinja because the two summaries can disagree
    about their key space — Monitoring supplies exact codes, the log-scan
    fallback only classes — and reconciling that in a template means writing
    the same conditional four times.
    """

    label: str
    # A 2xx row: the traffic that actually reached a page. The template
    # highlights these, because crawler 3xx/4xx outnumber them 10-50x and an
    # unmarked row with a smaller number in it reads as the boring one.
    served: bool
    count: int
    share: float | None
    recent: int


_CLASS_ORDER = ("2xx", "3xx", "4xx", "5xx")


def traffic_rows(window: RequestSummary, recent: RequestSummary) -> list[TrafficRow]:
    """Merge two windows into one ordered table body.

    Falls back to classes for both columns if either summary lacks exact
    codes, so the two columns are always counting the same kind of thing.
    """
    exact = bool(window.by_code) and bool(recent.by_code)
    # With classes, show all four even at zero — a missing 5xx row and a 5xx
    # row reading 0 say different things, and only one of them is true. With
    # exact codes, the union: a code seen only in the last half hour is
    # precisely the one worth surfacing.
    labels = sorted(set(window.by_code) | set(recent.by_code)) if exact else list(_CLASS_ORDER)
    long_counts = window.by_code if exact else window.by_class
    short_counts = recent.by_code if exact else recent.by_class
    return [
        TrafficRow(
            label=label,
            served=label.startswith("2"),
            count=long_counts.get(label, 0),
            share=(long_counts.get(label, 0) / window.total if window.total else None),
            recent=short_counts.get(label, 0),
        )
        for label in labels
    ]


_lock = threading.Lock()
_adapters: dict[str, AdapterStats] = {}
_summary_cache: dict[int, tuple[float, RequestSummary]] = {}
_host_split_cache: tuple[float, HostSplit] | None = None


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


def _query_request_summary_monitoring(
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> RequestSummary:
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

    The metric samples per minute and lands a minute or two behind, so a
    short window is missing its own most recent minute. Immaterial for the
    half-hour view, whose job is to show a surge that has already tripped an
    alert, not to be a live counter.
    """
    import os

    from google.cloud import monitoring_v3

    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT unset; cannot query monitoring")

    now = datetime.now(UTC)
    interval = monitoring_v3.TimeInterval(
        end_time=now, start_time=now - timedelta(minutes=window_minutes)
    )
    # One alignment bucket spanning the whole window collapses each series to a
    # single point, so the reducer hands back exactly one number per status.
    aggregation = monitoring_v3.Aggregation(
        alignment_period={"seconds": window_minutes * 60},
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
        window_minutes=window_minutes,
        source="cloud-monitoring",
        truncated=False,
    )


def _query_request_summary(window_minutes: int = DEFAULT_WINDOW_MINUTES) -> RequestSummary:
    """Single-shot Cloud Logging query — no caching, bounded work.

    Stops at `_REQUEST_SCAN_MAX_ENTRIES` or `_REQUEST_SCAN_DEADLINE_SECONDS`,
    whichever comes first, and flags the result `truncated` when it does.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=window_minutes)
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
        window_minutes=window_minutes,
        source="cloud-logging",
        truncated=truncated,
    )


# --- Which domain the traffic went to ----------------------------------------
#
# Cloud Monitoring cannot answer this: `response_count` carries only `loading`
# and `response_code`, and the `gae_app` resource has no host dimension. Only
# the request log knows the host, and it has no server-side count — so this is
# a sample, sized to a fixed number of lines rather than to a span of time.
#
# The cost is almost entirely the query's first page: measured against
# production, the first page takes ~4s to arrive and the 500 entries on it then
# parse in 0.02s. So the sample is as large as one page can carry.
#
# The deadline has to leave room for everything else /health does — two
# Monitoring queries and a snapshot read — inside gunicorn's 30s timeout. A
# 1000-line sample measured 7.6s against production; 10s is margin over that
# without putting the worker anywhere near being killed, which is the failure
# this endpoint has had once already.
_HOST_SAMPLE_ENTRIES = 1000
_HOST_SAMPLE_DEADLINE_SECONDS = 10.0
# `list_entries` defaults to ascending, which for "the most recent N" walks in
# from the far end of the retention window — the first attempt at this sampled
# entries old enough to have aged out and came back with nothing at all. This
# is the value of `google.cloud.logging.DESCENDING`, spelled out so the
# deferred import stays deferred.
_LOG_ORDER_DESCENDING = "timestamp desc"


class HostShare(BaseModel):
    """One domain's slice of a sampled stretch of traffic.

    Requests and page views are counted separately because they disagree, and
    the disagreement is the point: crawlers pile onto whichever domain is in
    their index, so a split of all requests is mostly a map of crawler
    attention. Measured 2026-08-24, the .com took 95% of requests and exactly
    half the page views.
    """

    host: str
    requests: int
    request_share: float
    page_views: int
    page_view_share: float


class HostSplit(BaseModel):
    """Rough traffic split by domain, measured over a fixed-size sample."""

    shares: list[HostShare] = Field(default_factory=list)
    sampled: int = 0
    page_views: int = 0
    # Oldest entry the sample reached, so the page can say how far back a
    # fixed number of lines happened to stretch.
    since: datetime | None = None
    # False when the log query failed; the page then says nothing rather than
    # showing a split of zero requests as though it meant something.
    available: bool = True


def _normalize_host(host: str, known: Iterable[str]) -> str:
    """Fold a request's Host header onto a configured domain.

    `www.` is the same site — it 301s to the apex before rendering anything.
    Everything else (appspot, an IP, a stale alias) is real traffic that isn't
    either site, and gets pooled rather than listed: the appspot hostname alone
    would otherwise take second place on a table about which domain people use.
    """
    host = host.split(":", 1)[0].lower().removeprefix("www.")
    return host if host in set(known) else "other"


def _cache_host_split(at: float, split: HostSplit) -> HostSplit:
    global _host_split_cache
    with _lock:
        _host_split_cache = (at, split)
    return split


def host_split(known_hosts: Iterable[str]) -> HostSplit:
    """Sample recent request logs and apportion them across `known_hosts`.

    Deliberately a sample of the most recent requests rather than of the whole
    window — a uniform day-wide sample would need the full scan this exists to
    avoid. Cached like the summaries; two seconds of log walking is not
    something to repeat on every reload.
    """
    now = time.time()
    with _lock:
        if _host_split_cache is not None and (
            now - _host_split_cache[0] < _REQUEST_SUMMARY_TTL_SECONDS
        ):
            return _host_split_cache[1]

    known = list(known_hosts)
    # Bounded below as well as ordered: without a timestamp floor the backend
    # has the whole retention window to consider, and the sample is meant to
    # describe traffic now.
    cutoff = datetime.now(UTC) - timedelta(minutes=DEFAULT_WINDOW_MINUTES)
    cutoff_iso = cutoff.replace(microsecond=0, tzinfo=None).isoformat() + "Z"
    filter_ = f'resource.type="gae_app" timestamp>="{cutoff_iso}" httpRequest.status>0'
    requests: dict[str, int] = {}
    page_views: dict[str, int] = {}
    sampled = 0
    served = 0
    since: datetime | None = None
    deadline = time.monotonic() + _HOST_SAMPLE_DEADLINE_SECONDS
    try:
        for entry in _logging_client().list_entries(
            filter_=filter_, order_by=_LOG_ORDER_DESCENDING, page_size=1000
        ):
            if sampled >= _HOST_SAMPLE_ENTRIES or time.monotonic() > deadline:
                break
            # The App Engine request log puts the Host header in the proto
            # payload, not in `http_request` — which carries the full URL but
            # is not always populated with it.
            payload = getattr(entry, "payload", None)
            if not isinstance(payload, dict):
                continue
            host = payload.get("host")
            if not host:
                continue
            sampled += 1
            since = getattr(entry, "timestamp", None) or since
            label = _normalize_host(host, known)
            requests[label] = requests.get(label, 0) + 1
            status = int(payload.get("status") or 0)
            if 200 <= status < 300:
                page_views[label] = page_views.get(label, 0) + 1
                served += 1
    except Exception:
        log.exception("host split sample failed")
        # Cached like a success: a source that is down should cost one slow
        # render every five minutes, not one on every reload.
        return _cache_host_split(now, HostSplit(available=False))

    if not sampled:
        return _cache_host_split(now, HostSplit(available=False))
    # Configured domains first in configured order, then the pool — so the two
    # rows worth comparing sit next to each other however the sample came out.
    order = [h for h in known if h in requests] + [h for h in requests if h not in set(known)]
    return _cache_host_split(
        now,
        HostSplit(
            shares=[
                HostShare(
                    host=h,
                    requests=requests[h],
                    request_share=requests[h] / sampled,
                    page_views=page_views.get(h, 0),
                    page_view_share=(page_views.get(h, 0) / served if served else 0.0),
                )
                for h in order
            ],
            sampled=sampled,
            page_views=served,
            since=since,
        ),
    )


def _ttl_seconds(window_minutes: int) -> float:
    if window_minutes <= _SHORT_WINDOW_MINUTES:
        return _RECENT_SUMMARY_TTL_SECONDS
    return _REQUEST_SUMMARY_TTL_SECONDS


def request_summary(window_minutes: int = DEFAULT_WINDOW_MINUTES) -> RequestSummary:
    """Recent HTTP traffic over `window_minutes`, cached in-process.

    Cloud Monitoring first: exact, server-side aggregated, and costing the
    same whether the site saw a hundred requests or a hundred thousand. The
    bounded log scan stands in when that fails — it under-reports during a
    traffic spike, which is when the page matters most, but a floor with a
    warning beats no number at all.

    Returns an empty summary with `source="unavailable"` only if both fail,
    so the health page degrades rather than 500ing.

    Each window caches separately — the page asks for two, and one must not
    serve the other's numbers.
    """
    now = time.time()
    with _lock:
        cached = _summary_cache.get(window_minutes)
        if cached is not None and now - cached[0] < _ttl_seconds(window_minutes):
            return cached[1]

    summary: RequestSummary | None = None
    try:
        summary = _query_request_summary_monitoring(window_minutes=window_minutes)
    except Exception:
        # Expected while `roles/monitoring.viewer` is missing, and whenever the
        # API is unreachable. Logged rather than raised so the scan can try.
        log.exception("cloud-monitoring request summary query failed; falling back")
        try:
            summary = _query_request_summary(window_minutes=window_minutes)
        except Exception:
            log.exception("cloud-logging request summary query failed too")
            return RequestSummary(window_minutes=window_minutes, source="unavailable")

    with _lock:
        _summary_cache[window_minutes] = (now, summary)
    return summary


def reset() -> None:
    """Clear all recorded state. Test-only helper."""
    global _host_split_cache
    with _lock:
        _adapters.clear()
        _summary_cache.clear()
        _host_split_cache = None
