"""Usage limits, so one visitor (or a script) cannot run up the bill.

Every question to the public demo can cost a call to Claude, so the demo has
three layers: a per-minute and a per-day limit for each visitor, and one daily
cap for the whole demo. A client with an API key has its own, higher limits.

Counters live in memory: they are per process and reset on every deploy or
restart. That is enough for one small server; with several servers, move them
to the database or Redis. Limits can be changed with environment variables.
"""
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class SlidingWindow:
    """Allows `limit` events per `seconds` for each key."""

    def __init__(self) -> None:
        self.events: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def hit(self, key: str, limit: int, seconds: int) -> int:
        """Record an event. Returns 0 if allowed, else the seconds to wait."""
        if limit <= 0:  # 0 = no limit
            return 0
        now = time.monotonic()
        with self.lock:
            q = self.events[key]
            while q and q[0] <= now - seconds:
                q.popleft()
            if len(q) >= limit:
                return max(1, int(q[0] + seconds - now) + 1)
            q.append(now)
            if len(self.events) > 5000:  # forget visitors that left
                for k in [k for k, v in self.events.items() if not v or v[-1] <= now - seconds]:
                    del self.events[k]
            return 0

    def clear(self) -> None:
        with self.lock:
            self.events.clear()


window = SlidingWindow()
DAY = 24 * 3600


def client_ip(request: Request) -> str:
    """The visitor's address. Behind a proxy (Render) it is in X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    return first or (request.client.host if request.client else "unknown")


def _refuse(wait: int, what: str) -> None:
    raise HTTPException(
        status_code=429,
        detail=f"Too many {what}. Try again in {wait} seconds.",
        headers={"Retry-After": str(wait)},
    )


def check_questions(request: Request, tenant_id: int | None) -> None:
    """Limit for /ask and /search. Raises 429 when exceeded."""
    if tenant_id is not None:
        who = f"tenant:{tenant_id}"
        per_min = _int("TENANT_PER_MINUTE", 30)
        per_day = _int("TENANT_PER_DAY", 1000)
    else:
        who = f"ip:{client_ip(request)}"
        per_min = _int("ANON_PER_MINUTE", 10)
        per_day = _int("ANON_PER_DAY", 100)
    for limit, seconds, label in ((per_min, 60, "requests"), (per_day, DAY, "requests today")):
        wait = window.hit(f"{who}:{seconds}", limit, seconds)
        if wait:
            _refuse(wait, label)
    if tenant_id is None:
        wait = window.hit("anon-global", _int("ANON_GLOBAL_PER_DAY", 1000), DAY)
        if wait:
            _refuse(wait, "requests to the demo today")


def check_uploads(tenant_id: int) -> None:
    wait = window.hit(f"upload:{tenant_id}", _int("UPLOADS_PER_HOUR", 60), 3600)
    if wait:
        _refuse(wait, "uploads")


def check_quality(tenant_id: int) -> None:
    """The quality check can make ~20 calls to Claude, so allow it only a few times an hour."""
    wait = window.hit(f"quality:{tenant_id}", _int("QUALITY_PER_HOUR", 3), 3600)
    if wait:
        _refuse(wait, "quality checks")


def check_widget(request: Request, tenant_id: int) -> None:
    """The widget is open to anyone who visits the client's site: limit the client
    as a whole and each visitor, so one visitor cannot use up the client's allowance."""
    check_questions(request, tenant_id)
    wait = window.hit(f"widget-ip:{tenant_id}:{client_ip(request)}:60", _int("WIDGET_VISITOR_PER_MINUTE", 8), 60)
    if wait:
        _refuse(wait, "requests")
