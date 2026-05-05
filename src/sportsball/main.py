import os
from pathlib import Path
from typing import Any

import yaml
from flask import Flask

# In production GAE merges env.yaml into the runtime environment before this
# code executes, so setdefault is a no-op there. Locally it loads dev secrets.
ENV_YAML_PATH = Path(__file__).resolve().parents[2] / "env.yaml"


def _load_env_yaml(path: Path = ENV_YAML_PATH) -> None:
    if not path.exists():
        return
    data: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    for key, value in (data.get("env_variables") or {}).items():
        os.environ.setdefault(key, str(value))


_load_env_yaml()

app = Flask(__name__)


@app.get("/")
def index() -> str:
    return "sportsball: scaffolding only — feed adapters and UI not yet wired up."


@app.get("/healthz")
def healthz() -> tuple[str, int]:
    return "ok", 200
