"""The two operator scripts in `bin/`.

Both wrap something with consequences — `bin/deploy` shells out to
`gcloud app deploy`, `bin/refresh` writes the production snapshot — so the
thing worth pinning is that asking them for help, or pointing them at a port
they don't own, does neither.

Every case runs with a PATH built from scratch, containing symlinks to only
the tools these scripts legitimately need. If the argument or port handling
regresses, the scripts fail with "not found" instead of deploying the app or
rewriting the live blob from a test run.
"""

import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Enough for the scripts to reach the guard being tested, and no more.
NEEDED = ("cat", "sed", "grep", "tr", "sleep", "curl", "lsof", "nc", "git")
# The two that make a regression expensive rather than merely red.
FORBIDDEN = ("gcloud", "uv")


@pytest.fixture(scope="session")
def sandbox_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A PATH assembled rather than assumed.

    An earlier version hardcoded `/usr/bin:/bin`, which is safe on the laptop
    this was written on — gcloud lives under ~/google-cloud-sdk there — and is
    a live deploy on GitHub's runners, where gcloud is /usr/bin/gcloud. CI
    caught it. Build the directory instead, so the guarantee holds anywhere.
    """
    sandbox = tmp_path_factory.mktemp("sandbox-bin")
    for name in NEEDED:
        found = shutil.which(name)
        if found:
            (sandbox / name).symlink_to(found)
    return str(sandbox)


def _run(
    sandbox_path: str, script: str, *args: str, **env: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(REPO / "bin" / script), *args],
        cwd=REPO,
        env={"PATH": sandbox_path, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_sandbox_really_is_a_sandbox(sandbox_path: str) -> None:
    """Guards the guard: if these become reachable, the tests below stop being
    safe to run and start being a deploy."""
    # Positive control first — a lookup that finds nothing proves nothing.
    assert shutil.which("cat", path=sandbox_path), "the lookup itself is broken"
    for binary in FORBIDDEN:
        assert shutil.which(binary, path=sandbox_path) is None, (
            f"{binary} is reachable from the test PATH; these tests would run it"
        )


def test_deploy_help_describes_the_wrapper_not_gcloud(sandbox_path: str) -> None:
    result = _run(sandbox_path, "deploy", "--help")
    assert result.returncode == 0, result.stderr
    assert "Usage: bin/deploy" in result.stdout
    # The reason to reach for it: the flag gcloud's own help can't mention.
    assert "--refresh" in result.stdout
    # And it should say where gcloud's help lives, having withheld it.
    assert "gcloud app deploy --help" in result.stdout


def test_deploy_accepts_the_short_help_flag(sandbox_path: str) -> None:
    assert _run(sandbox_path, "deploy", "-h").returncode == 0


def test_deploy_help_wins_wherever_it_appears(sandbox_path: str) -> None:
    """Someone appending --help to a command they already typed still gets
    help, rather than a deploy with an unrecognised flag."""
    result = _run(sandbox_path, "deploy", "--refresh", "--promote", "--help")
    assert result.returncode == 0
    assert "Usage: bin/deploy" in result.stdout


def test_refresh_help_does_not_write_the_snapshot(sandbox_path: str) -> None:
    """`bin/refresh` takes no arguments and used to ignore them, so asking it
    for help performed a production write."""
    result = _run(sandbox_path, "refresh", "--help")
    assert result.returncode == 0, result.stderr
    assert "Usage: bin/refresh" in result.stdout
    assert "REAL production blob" in result.stdout


def test_refresh_refuses_arguments_it_does_not_understand(sandbox_path: str) -> None:
    result = _run(sandbox_path, "refresh", "--dry-run")
    assert result.returncode == 2
    assert "takes no arguments" in result.stderr
    assert "--dry-run" in result.stderr


def test_refresh_refuses_a_port_that_is_already_in_use(sandbox_path: str) -> None:
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
        # Supplied explicitly: env.yaml is gitignored, so on a fresh clone the
        # bucket check would refuse first and this would pass for the wrong
        # reason. Nothing is written either way — it refuses at the port.
        result = _run(sandbox_path, "refresh", PORT=str(port), EVENTS_BUCKET="not-a-real-bucket")

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"Port {port} is already in use" in result.stderr
    # It must refuse *before* announcing a write to the production bucket.
    assert "Refreshing events into" not in result.stdout


def test_refresh_explains_itself_when_there_is_no_bucket(sandbox_path: str, tmp_path: Path) -> None:
    """A fresh clone has no env.yaml, and `set -e` used to kill the script on
    sed's failure — exit 2, no output, before the explanation could print."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    copied = bin_dir / "refresh"
    shutil.copy2(REPO / "bin" / "refresh", copied)

    result = subprocess.run(
        [str(copied)],
        cwd=tmp_path,
        env={"PATH": sandbox_path},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1, f"rc={result.returncode} out={result.stdout!r}"
    assert "EVENTS_BUCKET is unset" in result.stderr
    assert "silent no-op" in result.stderr
