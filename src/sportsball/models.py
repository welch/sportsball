from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, model_validator

# "home" means one of the teams this site exists for is playing at their own
# park: Giants at Oracle Park, Warriors or Valkyries at Chase Center. Anything
# else at a tracked venue — a concert, a monster truck rally, a college
# basketball doubleheader — is an "event".
#
# The distinction can't be inferred from a genre string. Ticketmaster files
# monster trucks under segment "Sports", which is exactly how they used to
# come out wearing the Warriors' color. It's the adapter that knows whether
# it's vouching for a home team, so the adapter is what sets this.
EventKind = Literal["home", "event"]

# Adapters that only ever emit home-team games. Used to bridge snapshots
# written before `kind` existed; see the validator below.
_HOME_SOURCES = frozenset({"mlb_statsapi", "espn_nba"})


class Event(BaseModel):
    source: str
    source_id: str
    name: str
    starts_at: datetime
    venue: str
    kind: EventKind = "event"
    # The venue announced a date but not a start time. `starts_at` is then
    # midnight local on that date — good enough to place the event on the
    # right day, but not a real clock time, so don't render it as one.
    time_tba: bool = False

    @model_validator(mode="before")
    @classmethod
    def _bridge_legacy_category(cls, data: Any) -> Any:
        """Accept snapshots written before `kind` replaced `category`.

        The stored blob is rewritten daily by cron, so this only matters for
        the hours between a deploy and the next refresh — but without it
        `read_events` would reject the whole payload and every cold start
        would hit the adapters directly. Valkyries games in a legacy blob
        come out as "event" rather than "home" (their old `category` said
        "sports" like everything else at Chase); one cron run corrects it.
        """
        if isinstance(data, dict) and "kind" not in data and "category" in data:
            return {**data, "kind": "home" if data.get("source") in _HOME_SOURCES else "event"}
        return data
