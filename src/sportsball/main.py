from flask import Flask

app = Flask(__name__)


@app.get("/")
def index() -> str:
    return "sportsball: scaffolding only — feed adapters and UI not yet wired up."


@app.get("/healthz")
def healthz() -> tuple[str, int]:
    return "ok", 200
