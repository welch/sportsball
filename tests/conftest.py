"""Test isolation from local env.yaml.

`sportsball.main` reads `env.yaml` at import time and pushes its keys into
`os.environ` (with `setdefault`, so they stay set across tests). That means
a developer's local secrets (e.g. `CANONICAL_HOST` for production, `VERB`)
would otherwise change test behaviour: every request would be 301-redirected
to the canonical host, and verb/title assertions would see the deployed
default instead of the framework default.

This autouse fixture clears the env vars that change request handling, so
each test starts from a clean slate. Tests that need them set use
`monkeypatch.setenv(...)` explicitly.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_env_from_local_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("CANONICAL_HOST", "HEALTH_TOKEN", "VERB", "EVENTS_BUCKET", "REPO_URL"):
        monkeypatch.delenv(key, raising=False)
