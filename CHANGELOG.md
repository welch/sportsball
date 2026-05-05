# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Pydantic `Event` model and Giants adapter against the MLB Stats API
  (`statsapi.mlb.com`). Returns one `Event` per game with venue name preserved
  so home/away can be filtered downstream by `venue == "Oracle Park"`.
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
