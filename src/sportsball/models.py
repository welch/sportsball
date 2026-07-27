from datetime import datetime
from typing import Literal

from pydantic import BaseModel

EventCategory = Literal["sports", "concert"]


class Event(BaseModel):
    source: str
    source_id: str
    name: str
    starts_at: datetime
    venue: str
    category: EventCategory = "sports"
    # The venue announced a date but not a start time. `starts_at` is then
    # midnight local on that date — good enough to place the event on the
    # right day, but not a real clock time, so don't render it as one.
    time_tba: bool = False
