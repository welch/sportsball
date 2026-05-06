# sportsball

### (aka "Is My Day @#$%#'d?")

_Sportsball_ is a one-page application that speaks  to the central question
of life in San Francisco's South Beach neighborhood:

_is my day going to be hosed by a Giants or Warriors home game
(or a Celine Dion concert at the Chase Center)?_

Tracks large events at:

- **Oracle Park** — SF Giants home games + concerts
- **Chase Center** — Golden State Warriors home games + concerts

## Local development

Requires [`uv`](https://docs.astral.sh/uv/) for local testing

```sh
uv sync                       # install deps into .venv
uv run pytest                 # run tests
uv run ruff check .           # lint
uv run ruff format .          # format
uv run flask --app sportsball.main run --debug
```

## Secrets

All secrets live in a single gitignored file: **`env.yaml`** at the repo root.
It serves both environments:

- **Locally**, the app reads it at startup and seeds `os.environ`.
- **In production**, `app.yaml`'s `includes: [env.yaml]` directive merges it
  into the GAE runtime environment at deploy time.

Format:

```yaml
env_variables:
  TICKETMASTER_API_KEY: <your-key>
  VERB: <default-verb-shown-in-page-title>
  CANONICAL_HOST: <your-canonical-host>
  # add more as needed
```

Variables:

- `TICKETMASTER_API_KEY` — required. Free Discovery API key from
  <https://developer.ticketmaster.com/>.
- `VERB` — optional. Default verb in the title ("Is my day _verb_?").
  Defaults to `hosed` if unset. URL-segment verbs (`/<verb>/`) still
  override per-request.
- `HEALTH_TOKEN` — required to view `/health/<token>`. 32-char hex value
  recommended (`python -c 'import secrets; print(secrets.token_hex(16))'`).
  Wrong-or-missing-token requests return 404 so the endpoint stays
  invisible to scanners. The page shows per-adapter status, the rolling
  24-hour HTTP request counts, and event-cache age — load it in a
  browser when you want a quick read on the deploy.
- `EVENTS_BUCKET` — optional. Cloud Storage bucket where the cron writes
  the canonical events snapshot (`events.json`) once a day. Serving
  instances read it on cold start, so the page's "last updated"
  timestamp reflects the cron time and instances skip the multi-adapter
  fetch. Leave unset locally — `_events()` falls back to fetching
  adapters directly. The GAE default bucket (`<project-id>.appspot.com`)
  is convenient: zero extra setup, and the App Engine default service
  account has write access by default.
- `CANONICAL_HOST` — optional. Bare hostname (no scheme, no path,
  e.g. `example.com`) to canonicalize on. When set, any request whose
  `Host` header doesn't match is 301-redirected to
  `https://$CANONICAL_HOST<path>?<query>`. `/healthz` and requests
  carrying `X-Appengine-Cron: true` are exempt so GAE health checks and
  cron keep working on the appspot host. Leave unset locally so dev
  requests on `localhost` aren't redirected.

## Deploy

Runs on Google App Engine's free/cheap tier (Standard environment, F1 auto-scaling)

```sh
gcloud app deploy app.yaml --project sports-ball
gcloud app deploy cron.yaml --project sports-ball
```

`cron.yaml` runs `/tasks/refresh` daily at 06:00 PT to keep the in-process
event cache warm. The endpoint is gated by the `X-Appengine-Cron` header
(GAE strips this from external requests), so only cron can trigger it.

`env.yaml` is bundled automatically — `.gitignore` keeps it out of git, but
`.gcloudignore` does NOT exclude it, so it ships with the deploy.

`requirements.txt` is the source of truth for the GAE python312 buildpack
(it doesn't read `uv.lock` directly). It's regenerated from `uv.lock` by a
pre-commit hook whenever the lock changes; CI verifies the two stay in
sync. Don't hand-edit it.
