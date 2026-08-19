"""The two operator scripts in `bin/`.

Both wrap something with consequences — `bin/deploy` shells out to
`gcloud app deploy`, `bin/refresh` writes the production snapshot — so the
thing worth pinning is that asking them for help does neither.

Every case runs with a PATH that contains neither `gcloud` nor `uv`. If the
argument handling ever regresses, these fail with "not found" instead of
deploying the app or rewriting the live blob from a test run.
"""

import shutil
import socket
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# `/usr/sbin` is here for lsof, which is how bin/refresh detects an occupied
# port. Still contains neither gcloud nor uv — see the sandbox test below.
SANDBOX_PATH = "/usr/bin:/bin:/usr/sbin"


def _run(script: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(REPO / "bin" / script), *args],
        cwd=REPO,
        env={"PATH": SANDBOX_PATH, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_sandbox_path_really_is_a_sandbox() -> None:
    """Guards the guard: if PATH ever picks these up, the tests below stop
    being safe to run and start being a deploy."""
    # Positive control first — a lookup that finds nothing proves nothing.
    assert shutil.which("cat", path=SANDBOX_PATH), "the lookup itself is broken"
    for binary in ("gcloud", "uv"):
        assert shutil.which(binary, path=SANDBOX_PATH) is None, (
            f"{binary} is reachable from the test PATH; these tests would run it"
        )


def test_deploy_help_describes_the_wrapper_not_gcloud() -> None:
    result = _run("deploy", "--help")
    assert result.returncode == 0, result.stderr
    assert "Usage: bin/deploy" in result.stdout
    # The reason to reach for it: the flag gcloud's own help can't mention.
    assert "--refresh" in result.stdout
    # And it should say where gcloud's help lives, having withheld it.
    assert "gcloud app deploy --help" in result.stdout


def test_deploy_accepts_the_short_help_flag() -> None:
    assert _run("deploy", "-h").returncode == 0


def test_deploy_help_wins_wherever_it_appears() -> None:
    """Someone appending --help to a command they already typed still gets
    help, rather than a deploy with an unrecognised flag."""
    result = _run("deploy", "--refresh", "--promote", "--help")
    assert result.returncode == 0
    assert "Usage: bin/deploy" in result.stdout


def test_refresh_help_does_not_write_the_snapshot() -> None:
    """`bin/refresh` takes no arguments and used to ignore them, so asking it
    for help performed a production write."""
    result = _run("refresh", "--help")
    assert result.returncode == 0, result.stderr
    assert "Usage: bin/refresh" in result.stdout
    assert "REAL production blob" in result.stdout


def test_refresh_refuses_arguments_it_does_not_understand() -> None:
    result = _run("refresh", "--dry-run")
    assert result.returncode == 2
    assert "takes no arguments" in result.stderr
    assert "--dry-run" in result.stderr


def test_refresh_refuses_a_port_that_is_already_in_use() -> None:
    """It drives whatever answers on the port, so the server has to be its own.

    It used to read "something answered /healthz" as "my server is up", and a
    leftover instance on that port — older code, same production bucket — was
    handed the refresh instead. That is not hypothetical: it happened, and the
    stale server wrote the live snapshot under the rules it was built with,
    erasing events the current code would have carried forward.
    """
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        result = _run("refresh", PORT=str(port))

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"Port {port} is already in use" in result.stderr
    # It must refuse *before* announcing a write to the production bucket.
    assert "Refreshing events into" not in result.stdout
