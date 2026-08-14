"""Test isolation from local env.yaml.

`sportsball.main` reads `env.yaml` at import time and pushes its keys into
`os.environ` (with `setdefault`, so they stay set across tests). That means
a developer's local secrets (e.g. the production `HOST_VERBS` map) would
otherwise change test behaviour: every request would be 301-redirected to
the primary host, and verb/title assertions would see the deployed default
instead of the framework default.

This autouse fixture clears the env vars that change request handling, so
each test starts from a clean slate. Tests that need them set use
`monkeypatch.setenv(...)` explicitly.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_env_from_local_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("HOST_VERBS", "HEALTH_TOKEN", "EVENTS_BUCKET", "REPO_URL"):
        monkeypatch.delenv(key, raising=False)
