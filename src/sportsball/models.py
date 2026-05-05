from datetime import datetime

from pydantic import BaseModel


class Event(BaseModel):
    source: str
    source_id: str
    name: str
    starts_at: datetime
    venue: str
