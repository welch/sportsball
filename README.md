# sportsball

### (aka "Is My Day @#$%#'d?")

_Sportsball_ is a two-page application that speaks to the central question
of life in San Francisco's South Beach and Mission Bay neighborhoods:

_is my day going to be hosed by a Giants or Warriors home game
(or a Celine Dion concert at the Chase Center)?_

Tracks large events via MLB, NBA, and Ticketmaster API's:

- [MLB Stats API](https://statsapi.mlb.com) — Giants schedule
- [NBA CDN](https://cdn.nba.com) league schedule — Warriors home games
- [Ticketmaster Discovery API](https://developer.ticketmaster.com) — once
  per venue, covering concerts, family shows, Valkyries, etc. (MLB and NBA
  game listings are filtered out so they don't duplicate the team feeds)

A cron job pulls them daily at 06:00 PT and persists a snapshot to
Google Cloud Storage. Serving instances read the snapshot on cold start.

URL patterns:

| Path | Effect |
|---|---|
| `/` | Today |
| `/2026-12-25` | Show what's scheduled for any other date |
| `/calendar/` | Month view of the current month |
| `/calendar/2026-12` | Month view of any month |
| `/health/<token>` | Operator status page (token-gated, 404s on mismatch) |

The verb in the title comes from the domain the request arrived on, so one
deployment can serve both a blunt domain and a polite one for locales with
sensitive ears. See `HOST_VERBS` below.

Navigate between day and month views by clicking a date on either.

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
  HOST_VERBS: <host=verb, …>     # optional
  NAV_HINTS: <host=style, …>     # optional
  EVENTS_BUCKET: <bucket-name>   # optional
  REPO_URL: <your-repo-url>      # optional
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
- `HOST_VERBS` — optional. The domains this deployment answers to and the
  verb each renders in the title ("Is my day _verb_?"), as a comma-separated
  list of `host=verb` pairs with bare hostnames (no scheme, no path):

  ```yaml
  HOST_VERBS: ismydayfucked.com=fucked, ismydayhosed.fun=hosed
  ```

  One deployment serves them all; the pages differ only in the word, and
  each domain canonicalizes to itself so neither outranks the other. Any
  host *not* in the list is 301-redirected to the first entry, which
  consolidates `<project>.appspot.com`, bare IPs, and stale aliases onto
  the real domain. `/healthz`, `localhost`/`127.0.0.1`/`0.0.0.0` requests,
  and requests carrying `X-Appengine-Cron: true` are exempt, so GAE health
  checks, local dev, and cron keep working on the appspot host. Unmapped
  hosts render the first entry's verb, so local dev looks like the live
  site; to preview another domain's wording without touching DNS, send its
  Host header: `curl -H 'Host: ismydayhosed.fun' localhost:5000`. With the
  variable unset there's no redirect at all and the verb is `hosed`.
- `NAV_HINTS` — optional. Per-domain navigation affordances, same
  `host=value` grammar as `HOST_VERBS`, where the value is a style the
  stylesheet knows. One ships: `chevron`, a dotted rule that goes solid on
  hover, trailing the `›` the calendar's pager already uses.

  ```yaml
  NAV_HINTS: ismydayhosed.fun=chevron
  ```

  The site's links are undressed by default, which suits a page someone
  visits daily and works against one they've never seen — a first-time
  visitor can't tell it's anything but a picture. Turning hints on for a
  single domain dresses the links that carry a `nav-link` class (the date
  → month, the footer → about) without disturbing the everyday domain.
  Append `?nav=chevron` or `?nav=off` to any page to try a style without a
  restart; the canonical tag ignores the query, so those variants stay out
  of search. Adding a style means a `.nav-<name>` block in `8ball.css` and
  a word in `NAV_HINT_STYLES`. Unlisted hosts, unknown styles, and an unset
  variable all render the plain look.
- `EVENTS_BUCKET` — optional. Cloud Storage bucket where the cron writes
  the canonical events snapshot. The GAE default bucket
  (`<project-id>.appspot.com`) is convenient: it exists by default and the
  App Engine default service account already has write access. Without
  this, `_events()` falls back to fetching adapters directly on every cold
  start.
- `REPO_URL` — optional. Base URL of *your* copy of the source, e.g.
  `https://github.com/you/sportsball` (no trailing slash needed). The health
  page turns the running build string into a link to the exact commit it was
  deployed from, `$REPO_URL/commit/<sha>`.

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
bin/deploy --refresh --project=<your-project-id> # …and rebuild the snapshot
gcloud app deploy cron.yaml --project=<your-project-id>
```

`bin/deploy` is a thin wrapper around `gcloud app deploy app.yaml` that
encodes the current git state (tag, short SHA, clean/dirty) into the
GAE version ID.  A bare `gcloud app deploy app.yaml` gets you a staging
deployment with a timestamp version ID — health page will display that raw.

`cron.yaml` runs `/tasks/refresh` daily at 06:00 PT, which fetches all
sources and writes a fresh snapshot to `EVENTS_BUCKET`. The endpoint is
gated by the `X-Appengine-Cron` header (GAE strips this from external
requests), so nothing on the internet can trigger it — including you.

To force a refresh rather than wait for 06:00, run `bin/refresh`, or pass
`--refresh` to `bin/deploy` to do it as soon as the deploy lands. Nothing
strips that header locally, so the script starts the app on a loopback
port, sends the header itself, and lets the ordinary handler fetch every
adapter and write the snapshot. Deployed instances pick it up within a
minute: each one polls the blob's GCS generation number on that interval and
re-downloads only when it has changed, so a manual refresh reaches warm
instances without waiting out their 12-hour cache.

Two things to know before using it. It writes the real production blob —
the adapters run on your machine and your credentials do the write. And run
it *after* deploying, never before: a snapshot written by newer code can
carry fields the deployed version doesn't understand yet. `bin/deploy
--refresh` sequences it correctly, and refuses to refresh if the deploy
fails.

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
