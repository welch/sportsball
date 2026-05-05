# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
