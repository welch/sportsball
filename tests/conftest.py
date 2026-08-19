"""Test isolation: from local env.yaml, and from Google Cloud.

`sportsball.main` reads `env.yaml` at import time and pushes its keys into
`os.environ` (with `setdefault`, so they stay set across tests). That means
a developer's local secrets (e.g. the production `HOST_VERBS` map) would
otherwise change test behaviour: every request would be 301-redirected to
the primary host, and verb/title assertions would see the deployed default
instead of the framework default.

The first autouse fixture clears the env vars that change request handling,
so each test starts from a clean slate. Tests that need them set use
`monkeypatch.setenv(...)` explicitly.

The second stops the suite reaching Google Cloud at all. Nothing here should
depend on credentials, network, or somebody's live project — and when the
health summary gained a Cloud Monitoring path, the tests that had only
patched the logging client quietly started making real API calls, which took
the suite from under a second to nearly thirty. Both clients raise by default;
a test that wants one patches it.
"""

import pytest

from sportsball import stats


@pytest.fixture(autouse=True)
def _isolate_env_from_local_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("HOST_VERBS", "HEALTH_TOKEN", "EVENTS_BUCKET", "REPO_URL"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _no_google_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(which: str):
        def _refuse(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError(
                f"test reached the real {which} client; patch it in the test instead"
            )

        return _refuse

    monkeypatch.setattr(stats, "_monitoring_client", refuse("Cloud Monitoring"))
    monkeypatch.setattr(stats, "_logging_client", refuse("Cloud Logging"))
