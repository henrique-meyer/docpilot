"""Public abuse limits shared by /ask and /agent."""

from __future__ import annotations

import threading
import time
from datetime import date


class QuestionTooLong(Exception):
    pass


class RateLimited(Exception):
    pass


class DemoLimitReached(Exception):
    pass


class DemoLimits:
    def __init__(
        self,
        *,
        max_question_chars: int,
        rate_limit: int,
        window_seconds: int,
        daily_token_cap: int,
    ) -> None:
        self.max_question_chars = max_question_chars
        self.rate_limit = rate_limit
        self.window_seconds = window_seconds
        self.daily_token_cap = daily_token_cap
        self._calls: dict[str, list[float]] = {}
        self._tokens = 0
        self._day = date.today()
        self._lock = threading.Lock()

    def check_question(self, question: str) -> None:
        if len(question) > self.max_question_chars:
            raise QuestionTooLong

    def consume_request(self, client_ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            recent = [
                stamp
                for stamp in self._calls.get(client_ip, [])
                if now - stamp < self.window_seconds
            ]
            if len(recent) >= self.rate_limit:
                self._calls[client_ip] = recent
                raise RateLimited
            recent.append(now)
            self._calls[client_ip] = recent
            self._roll_day()
            if self._tokens >= self.daily_token_cap:
                raise DemoLimitReached

    def add_tokens(self, count: int) -> None:
        if count < 1:
            return
        with self._lock:
            self._roll_day()
            self._tokens += count

    def _roll_day(self) -> None:
        today = date.today()
        if today != self._day:
            self._day = today
            self._tokens = 0
