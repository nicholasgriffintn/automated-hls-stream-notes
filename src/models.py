from dataclasses import dataclass
from datetime import datetime
from typing import List

@dataclass
class Note:
    timestamp: datetime
    content: str
    category: str
    key_points: List[str]
    entities: List[str]
    importance: int

@dataclass
class IntervalSummary:
    start_time: datetime
    end_time: datetime
    key_points: List[str]
    main_topics: List[str]
    important_events: List[str]
    action_items: List[str] 