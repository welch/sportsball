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
