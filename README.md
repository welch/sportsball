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

## Deploy

```sh
gcloud app deploy app.yaml --project sports-ball
gcloud app deploy cron.yaml --project sports-ball
```
