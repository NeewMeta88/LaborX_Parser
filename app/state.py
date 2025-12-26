from collections import deque
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class RuntimeState:
    running: bool = False
    target_chat_id: Optional[int] = None
    last_seen_href: Optional[str] = None
    seen_set: set[str] = None
    seen_order: deque = None
    sent_count: int = 0
    last_error: Optional[str] = None
    or_limit: Optional[int] = None
    or_remaining: Optional[int] = None
    or_reset_ms: Optional[int] = None
    ai_used_today: int = 0
    ai_utc_day: Optional[date] = None
    startup_chat_id: int | None = None
    startup_message_id: int | None = None
    max_seen_job_id: int = 0

    def __post_init__(self):
        if self.seen_set is None:
            self.seen_set = set()
        if self.seen_order is None:
            self.seen_order = deque()
