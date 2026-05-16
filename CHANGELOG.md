# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- "...but what about <random MLB city>?" — the about page's "other cities"
  header now picks a random MLB home city per request from the 27 current
  homes (San Francisco intentionally excluded). Refreshing the page is
  surprisingly entertaining.
- Hero photo on `/about`: heavy game-day traffic outside Oracle Park with
  a singer mid-song atop a car. Placed directly under the centered title;
  the rest of the page reads as commentary on the picture. ~260 KB JPEG,
  cache-busted like the other static assets.

### Changed

- `/about` blockquote no longer name-checks a specific artist — just
  "a concert at the Chase Center" so the joke doesn't date.
- Defensive headers on the other two upstream adapters too. Giants (MLB
  Stats API, also Akamai-fronted) now sends a Chrome-shaped UA plus
  `Origin`/`Referer` for `www.mlb.com`. Ticketmaster Discovery API gets
  a `sportsball/0.5.0` UA and explicit `Accept: application/json` — the
  API key already authenticates us so no need to masquerade as a browser
  there. Verified all four adapters still 200 with the new headers and
  return their expected event counts.

### Fixed

- Warriors adapter unblocked from Akamai's WAF in front of `cdn.nba.com`.
  The endpoint started 403'ing requests that didn't look like real
  nba.com-fetched XHRs. Adding browser-context headers (Chrome-shaped
  User-Agent, `Origin: https://www.nba.com`, `Referer: https://www.nba.com/`,
  `Accept`, `Accept-Language`, and the `sec-ch-ua` / `sec-fetch-*` signals
  the actual nba.com client sends) gets us through. Tested live: 89 GSW
  events fetched.
- Cron now preserves historical `last_success_at` for adapters that fail
  this run. The handler loads the prior snapshot's `adapter_stats` before
  calling `fetch_all`, so `record_adapter_failure(...)` keeps the
  previous success metadata. Before this, a single bad day wiped out
  "when did this adapter last work?" on the health page — exactly the
  signal we needed when cdn.nba.com started 403'ing.

### Added

- Cloud Storage backing for the events snapshot. Cron now fetches all
  adapters, writes the resulting events list + `fetched_at` timestamp
  to `gs://$EVENTS_BUCKET/events.json` (one ~200 KB JSON blob), and
  updates the local cache. Serving instances read that blob on cold
  start instead of re-fetching adapters individually — cuts a fresh
  instance's first-request latency from a multi-second multi-adapter
  fetch (the NBA league feed alone is ~8 MB) to a single small
  Storage read.
- `_events()` reads from Cloud Storage on cache miss and falls back to
  direct adapter fetching when the bucket is unset (local dev) or the
  blob is missing/unreadable. Local dev keeps working.

### Changed

- The page's "last updated" timestamp now reflects the cron's
  `fetched_at` (read out of the stored blob), not each instance's own
  first-fetch time. So a fresh 9 AM instance still shows "last updated
  06:00" — matching the actual upstream-data refresh frequency.
- `tests/conftest.py` now clears env-driven configuration (`VERB`,
  `CANONICAL_HOST`, `HEALTH_TOKEN`, `EVENTS_BUCKET`) before each test
  so the suite is hermetic regardless of the developer's local `env.yaml`.
- Cron now persists not just `(events, fetched_at)` but also
  `previously_unseen` — the events whose `(source, source_id)` wasn't
  in the prior cron's snapshot. The health page renders that list as a
  "New since previous cron" table so you can see exactly what each cron
  brought in. First-ever run flags everything as new (no prior to diff
  against).

### Added

- `/about` page — plain-language explanation of the site for casual
  visitors. The "last updated" footer link in the page footer now points
  here instead of straight at the GitHub repo. The repo link lives at
  the bottom of `/about` for anyone who wants the code.
- "Page views" tile on the health page — a separate count of 2xx
  responses in the last 24h, distinct from the all-status total. Most
  3xx hits are unfollowed canonical-host redirects from legacy-URL bot
  probes, and most 4xx hits are vulnerability scanners. The 2xx-only
  number is the real "humans loaded my page" metric.
- `bin/deploy` — wrapper around `gcloud app deploy app.yaml` that
  encodes the current git state (`<tag>-<sha>-<clean|dirty>`) into the
  GAE version ID. The deployed app reads `$GAE_VERSION` (auto-set by GAE)
  and renders it on the health page, so "what's actually running" is
  visible at a glance. Bare `gcloud app deploy` still works; the
  timestamp-shaped ID it produces is shown raw, which itself signals
  "this wasn't deployed via bin/deploy."

### Fixed

- Per-adapter stats (last success / last failure / event count) now
  survive instance lifecycle. Cron persists a `snapshot_adapter_stats()`
  alongside the events blob; the storage-read path calls
  `stats.load_adapter_stats(...)` so any serving instance reflects the
  cron's view of adapter health. Without this the health page on a
  fresh-after-scale-to-zero instance showed every adapter as "never
  ran" — accurate-but-misleading because the cron *had* run, just on
  an instance that no longer existed.
- HTTP-request counts on the health page are now queried from Cloud
  Logging (with a 5-minute in-process cache) rather than tracked in an
  in-process deque. The deque was per-instance and reset to zero every
  time GAE scaled to zero, which made the count non-monotonic and
  effectively useless. Cloud Logging is GAE's existing log stream — no
  new infrastructure to manage. The query filter excludes `/health/`,
  `/healthz`, and `/tasks/refresh` so operator polling, GAE health
  probes, and cron invocations don't inflate the user-traffic count.
  Adds a `roles/logging.viewer` IAM grant requirement for the App Engine
  default service account; documented in the README setup section.
- Canonical-host redirect now skips requests whose `Host` is `localhost`,
  `127.0.0.1`, or `0.0.0.0`, so a developer with the production
  `CANONICAL_HOST` in their local `env.yaml` can still hit
  `http://localhost:PORT` without being bounced to production.

## [0.4.0] - 2026-05-06

### Added

- `/health/<token>` status page, gated by the `HEALTH_TOKEN` env var
  (wrong/missing token 404s — never reveals the endpoint exists). Renders
  a plain HTML snapshot of: per-adapter last-success / last-failure
  timestamps + event count + error message; rolling 24-hour HTTP request
  totals broken down by status-code class (2xx/3xx/4xx/5xx); cached event
  count and last-refresh age in human-friendly form. Backed by a new
  `sportsball.stats` module — thread-safe in-process telemetry over a
  pruned `deque`, no new dependencies. `aggregator.fetch_all` now takes
  named adapter tuples and records success/failure per source. The health
  endpoint itself is excluded from the request counter so reloading it
  doesn't pollute the numbers.
- 301-redirect non-canonical hosts to the host configured via the
  `CANONICAL_HOST` env var. `before_request` hook reads the canonical
  bare hostname from `os.environ` and redirects to
  `https://$CANONICAL_HOST<path>?<query>` whenever `request.host`
  differs. No-ops when the env var is unset (local dev). `/healthz` and
  requests with `X-Appengine-Cron: true` are exempt so GAE health checks
  and cron continue to work on the appspot host. Busts stale 301s
  cached by browsers from the previous reverse-direction redirect.
- Pulsing-glow effect on the 8-ball's answer window. A duplicate of the
  ball image is layered over the base, clipped to a circle around the
  answer window (`circle(23% at 50% 47%)`), and animated with a 3-second
  filter pulse (drop-shadow + brightness + contrast) that holds at peak
  for a third of the cycle. The base image stays static, so only the
  "SIGNS POINT TO YES" / "OUTLOOK NOT SO GOOD" / etc. region shimmers.
- Footer "last updated …" timestamp now links to the GitHub repo
  (`https://github.com/welch/sportsball`). Anchor opens in a new tab
  (`target="_blank"` + `rel="noopener noreferrer"`) and inherits the
  footer's grey color with no underline across all link states, so the
  footer looks identical to before — just clickable.

## [0.3.0] - 2026-05-05

### Added

- `/tasks/refresh` endpoint that invalidates the in-memory event cache
  and refills it. Gated by the `X-Appengine-Cron` header (GAE strips this
  from external requests), so external callers can't trigger refetch
  storms. `cron.yaml` schedules it daily at 06:00 PT.
- Page-date row under the 8-ball: full date in the title font, colored
  using the same day-color (Giants orange / Warriors blue / concert
  purple) as the verb and halo. Aids in eyeballing arbitrary `/<isodate>`
  pages.
- Footer: small grey "last updated &lt;ts&gt;" timestamp showing when the
  in-process event cache was last refreshed. Doubles as visible
  verification that `/tasks/refresh` actually ran.

### Fixed

- Halo radial-gradient stops were being measured against the wrapper's
  diagonal (`farthest-corner`, the CSS default), which placed inner-ring
  colors fully behind the 8-ball image. Switched to `closest-side` so
  percentages correspond to the visible halo zone (60–100%); two- and
  three-halo days now show all rings as intended.
- Static-asset cache-busting. All `<link>`/`<img>` URLs now carry a
  `?v=<hash>` query, where `<hash>` is a content hash of every file in
  the static directory. Computed once at startup. When any CSS or image
  changes, the hash rotates and browsers refetch automatically — no more
  manual cache-clearing after deploys.
- Ticketmaster Discovery adapter, single module covering both venues
  (`fetch_oracle_park_events()` / `fetch_chase_center_events()`). Filters
  out `subGenre` `MLB` and `NBA` so it doesn't duplicate the team adapters;
  WNBA Valkyries home games and Motorsports/Racing events pass through.
  Reads `TICKETMASTER_API_KEY` from `os.environ`.
- Per-adapter resilience in `aggregator.fetch_all()` — one failing source
  no longer blanks the page; the failure is logged and the remaining
  adapters' events render. Ticketmaster's API has been observed returning
  502s on retry, so this isn't theoretical.
- `Event.category` (Literal `"sports"` | `"concert"`) and per-category
  page theming. Sports events keep their team color (Giants orange at
  Oracle Park, Warriors blue at Chase Center). Concert/non-sports events
  get a third color (purple `#7E2F8E`) for both verb and halo. WNBA
  Valkyries are tagged `sports` (they're a team game) and share the
  Warriors blue. Halos compose: a day with a Giants game and a concert
  shows orange + purple concentric rings.

## [0.2.0] - 2026-05-05

### Added

- Production deploy on GAE Standard python312 working at
  `https://sports-ball.appspot.com/`. Required:
  - Generate `requirements.txt` from `uv.lock` (the python312 buildpack
    reads `requirements.txt`, not `uv.lock`). Pre-commit hook keeps it
    in sync; CI fails on drift.
  - `--pythonpath src` in the gunicorn entrypoint so the `src/` package
    layout resolves at runtime.
  - One-time IAM grant of `roles/cloudbuild.builds.builder` to the App
    Engine default service account on this older project (modern projects
    auto-grant; pre-second-gen ones don't).
  - One-time enable of `artifactregistry.googleapis.com` and
    `cloudbuild.googleapis.com`.
- `VERB` env var sets the default verb in the page title, read at request
  time. Falls back to `hosed` when unset. URL-segment verbs still override
  per-request. Lets the rude default stay out of git via `env.yaml`.
- 8-ball page wired up over the multi-adapter aggregator, in three states:
  - **Today fucked**: lists every event happening today; ends with
    "No peace and quiet until [tomorrow / Monday / Monday, May 12]."
  - **All clear, future event scheduled**: title-sized line "All clear
    until [day/date]:" with each upcoming event in parentheses.
  - **All clear, nothing scheduled**: "All clear. No future events scheduled."
- URL routes: `/`, `/<verb>/`, `/<isodate>`, `/<verb>/<isodate>`. `<verb>`
  is letters-only; `<isodate>` is `YYYY-MM-DD` and 404s on invalid dates.
  Querying a date treats that date as "today" for status computation.
- Page modernizations vs the v0 port: mobile viewport meta, semantic
  `<ul>`/`<li>`/`<time>` markup, color tied to active venue (Giants
  orange / Warriors blue applied only to the verb in the title and to
  the team names "San Francisco Giants" / "Golden State Warriors" inline).
- Per-venue halo around the 8-ball — radial-gradient glow showing which
  team(s) own the day. Two-team days draw concentric halos (orange inner,
  blue outer). Halo only renders on today-fucked days.
- `aggregator` module: `fetch_all()` rolls up adapters and filters to
  tracked venues; `compute_status()` returns a `Status` with today's
  events, the next future-event date, and the next quiet date.
- 12-hour in-process cache around the adapter fetch (NBA league feed is
  ~8 MB; refetching per request would be unworkable).
- Pydantic `Event` model and Giants adapter against the MLB Stats API
  (`statsapi.mlb.com`). One `Event` per game with venue name preserved
  so home/away is filterable by `venue == "Oracle Park"`.
- Warriors adapter against the NBA league-wide CDN feed
  (`cdn.nba.com/static/json/staticData/scheduleLeagueV2.json`). Filters to
  GSW; same `Event` shape as Giants. Home/away by `venue == "Chase Center"`.
- Secrets pattern: single gitignored `env.yaml` at the repo root, deployed
  to GAE via `app.yaml` `includes:` and loaded into `os.environ` at app
  startup locally. One source of truth, no `.env`/YAML duplication.

### Removed

- `icalendar` dependency. None of the four planned feeds use iCal.

## [0.1.0] - 2026-05-04

### Added

- Initial repository scaffolding: `uv` project, `ruff` + `ty` + `pytest` + `pre-commit`,
  GitHub Actions CI, GAE `app.yaml` / `cron.yaml`, MIT license.
- Minimal Flask skeleton (`/`, `/healthz`) so the deploy target builds and serves.
