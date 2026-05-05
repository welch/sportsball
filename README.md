# sportsball

_Is my day going to be hosed by a sportsball game or a stadium concert?_

Tracks large events (>100 people) at:

- **Oracle Park** — SF Giants home games + concerts
- **Chase Center** — Golden State Warriors home games + concerts

Hosted at <http://sports-ball.appspot.com/>.

## Status

Rewrite-in-progress. The previous Python 2.7 / `webapp2` version lives at
[`welch/sportsball-v0`](https://github.com/welch/sportsball-v0).

## Local development

Requires [`uv`](https://docs.astral.sh/uv/).

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
  # add more as needed
```

Variables:

- `TICKETMASTER_API_KEY` — required. Free Discovery API key from
  <https://developer.ticketmaster.com/>.
- `VERB` — optional. Default verb in the title ("Is my day _verb_?").
  Defaults to `hosed` if unset. URL-segment verbs (`/<verb>/`) still
  override per-request.

## Deploy

```sh
gcloud app deploy app.yaml --project sports-ball
gcloud app deploy cron.yaml --project sports-ball
```

`env.yaml` is bundled automatically — `.gitignore` keeps it out of git, but
`.gcloudignore` does NOT exclude it, so it ships with the deploy.
