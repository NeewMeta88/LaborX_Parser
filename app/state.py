from dataclasses import dataclass
from collections import deque
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

    def __post_init__(self):
        if self.seen_set is None:
            self.seen_set = set()
        if self.seen_order is None:
            self.seen_order = deque()