# sportsball

### (aka "Is My Day @#$%#'d?")

_Sportsball_ is a one-page application that speaks to the central question
of life in San Francisco's South Beach and Mission Bay neighborhoods:

_is my day going to be hosed by a Giants or Warriors home game
(or a Celine Dion concert at the Chase Center)?_

A big Magic-8-Ball gives the verdict; small print explains why.

Tracks large events at:

- **Oracle Park** — SF Giants home games + concerts
- **Chase Center** — Golden State Warriors home games, Golden State Valkyries
  (WNBA) home games, and concerts

## How it works

Four upstream sources feed the page:

- [MLB Stats API](https://statsapi.mlb.com) — Giants schedule
- [NBA CDN](https://cdn.nba.com) league schedule — Warriors home games
- [Ticketmaster Discovery API](https://developer.ticketmaster.com) — once
  per venue, covering concerts, family shows, Valkyries, etc. (MLB and NBA
  game listings are filtered out so they don't duplicate the team feeds)

A cron job pulls from all four daily at 06:00 PT, normalizes everything
into a common `Event` shape, computes the diff against the previous run,
and persists a snapshot to Google Cloud Storage. Serving instances read
the snapshot on cold start, so the "last updated" timestamp reflects the
cron run rather than each instance's first fetch.

URL patterns:

| Path | Effect |
|---|---|
| `/` | Today |
| `/<verb>/` | Same, with a custom verb (`@#$%&!!!`, `screwed`, …) |
| `/2026-12-25` | Show what's scheduled for any other date |
| `/<verb>/2026-12-25` | Both |
| `/calendar/` | Month view of the current month |
| `/calendar/2026-12` | Month view of any month |
| `/<verb>/calendar/2026-12` | Both |
| `/health/<token>` | Operator status page (token-gated, 404s on mismatch) |

The calendar rings each day in the same colors the 8-ball halo uses — orange
for the Giants, blue for Chase Center sports, purple for concerts, stacked
concentrically when a day has more than one. Clicking the date under the
8-ball opens that month; clicking a day in the calendar goes back to the
8-ball for that date. A verb in the path is carried across both hops.

## Run your own

Requires [`uv`](https://docs.astral.sh/uv/) for Python tooling and the
[`gcloud` CLI](https://cloud.google.com/sdk/docs/install) for deployment.

### Local development

```sh
uv sync                       # install deps into .venv
uv run pytest                 # run tests
uv run ruff check .           # lint
uv run ruff format .          # format
uv run flask --app sportsball.main run --debug    # http://localhost:5000
```

### Configuration

All configuration and secrets live in a single gitignored file: **`env.yaml`**
at the repo root. It serves both environments:

- **Locally**, the app reads it at startup and seeds `os.environ`.
- **In production**, `app.yaml`'s `includes: [env.yaml]` directive merges it
  into the GAE runtime environment at deploy time.

Format:

```yaml
env_variables:
  TICKETMASTER_API_KEY: <your-key>
  HEALTH_TOKEN: <32-char-hex>
  VERB: <default-verb>           # optional
  CANONICAL_HOST: <your-host>    # optional
  EVENTS_BUCKET: <bucket-name>   # optional
```

Variables:

- `TICKETMASTER_API_KEY` — required. Free Discovery API key from
  <https://developer.ticketmaster.com/>.
- `HEALTH_TOKEN` — required to view `/health/<token>`. 32-char hex
  recommended (`python -c 'import secrets; print(secrets.token_hex(16))'`).
  Wrong-or-missing-token requests return 404 so the endpoint stays invisible
  to scanners. The page shows per-adapter status, the rolling 24-hour HTTP
  request counts, the cache age, and the events that arrived in the most
  recent cron run.
- `VERB` — optional. Default verb in the title ("Is my day _verb_?").
  Defaults to `hosed` if unset. URL-segment verbs (`/<verb>/`) still
  override per-request.
- `CANONICAL_HOST` — optional. Bare hostname (no scheme, no path,
  e.g. `example.com`) to canonicalize on. When set, any request whose
  `Host` header doesn't match is 301-redirected to
  `https://$CANONICAL_HOST<path>?<query>`. `/healthz`, `localhost`/`127.0.0.1`
  /`0.0.0.0` requests, and requests carrying `X-Appengine-Cron: true` are
  exempt so GAE health checks, local dev, and cron all keep working on the
  appspot host. Leave unset locally.
- `EVENTS_BUCKET` — optional. Cloud Storage bucket where the cron writes
  the canonical events snapshot. The GAE default bucket
  (`<project-id>.appspot.com`) is convenient: it exists by default and the
  App Engine default service account already has write access. Without
  this, `_events()` falls back to fetching adapters directly on every cold
  start.

### First-time GCP setup

If you're forking and deploying to your own project, the first deploy needs
these one-time steps:

```sh
PROJECT=<your-project-id>
SA=${PROJECT}@appspot.gserviceaccount.com

# Create the App Engine app (one-time; pick a region when prompted).
gcloud app create --project=$PROJECT

# APIs the python312 buildpack and Cloud Scheduler need.
gcloud services enable \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  --project=$PROJECT

# IAM: let the App Engine default service account run Cloud Build, write
# to the default storage bucket, and read its own request logs (so the
# health page can render real 24h HTTP traffic stats). Modern projects
# auto-grant the first two; older ones don't.
gcloud projects add-iam-policy-binding $PROJECT \
  --member=serviceAccount:$SA \
  --role=roles/cloudbuild.builds.builder
gcloud storage buckets add-iam-policy-binding gs://$PROJECT.appspot.com \
  --member=serviceAccount:$SA \
  --role=roles/storage.objectAdmin
gcloud projects add-iam-policy-binding $PROJECT \
  --member=serviceAccount:$SA \
  --role=roles/logging.viewer
```

If you want a custom domain, see
[GAE custom domains](https://cloud.google.com/appengine/docs/standard/mapping-custom-domains).

### Deploy

```sh
bin/deploy --project=<your-project-id>           # deploys app.yaml
gcloud app deploy cron.yaml --project=<your-project-id>
```

`bin/deploy` is a thin wrapper around `gcloud app deploy app.yaml` that
encodes the current git state (tag, short SHA, clean/dirty) into the
GAE version ID. The runtime then reads `$GAE_VERSION` and shows it on
the health page, so "what's actually running" is one click away.

A bare `gcloud app deploy app.yaml` still works but assigns a timestamp
version ID — health page will display that raw, which is itself a clear
"this wasn't deployed via bin/deploy" signal.

`cron.yaml` runs `/tasks/refresh` daily at 06:00 PT, which fetches all four
sources and writes a fresh snapshot to `EVENTS_BUCKET`. The endpoint is
gated by the `X-Appengine-Cron` header (GAE strips this from external
requests), so only cron can trigger it.

`env.yaml` is bundled with the deploy automatically — `.gitignore` keeps it
out of version control, but `.gcloudignore` does NOT exclude it, so it
ships with the deploy.

`requirements.txt` is the source of truth for the GAE python312 buildpack
(it doesn't read `uv.lock` directly). It's regenerated from `uv.lock` by a
pre-commit hook whenever the lock changes; CI verifies the two stay in
sync. Don't hand-edit it.

### Hosting cost

Runs comfortably within Google App Engine's daily free quotas (Standard
environment, F1 auto-scaling, scale-to-zero). For a personal-traffic
site this means $0/month. A sustained Hacker News spike or aggressive
scraping could push the F1 instance hours past 28/day and start billing
at ~$0.05/hour, but you'd have to work at it.
