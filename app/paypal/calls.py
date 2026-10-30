"""The PayPal call log: what the ticker shows and what goes on the audit chain as paypal.call.

Records hold the path TEMPLATE, status, duration and debug id. Never a header, token, body, id or email, so there is
nothing secret to leak. The per-request "scope" says whose call it is: it lets the ticker count money-moving calls per
session, and lets the simulator apply a visitor's own fault injection.
"""
from __future__ import annotations

import contextvars
import threading
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass

_scope = contextvars.ContextVar("paypal_scope", default=None)


@dataclass
class Scope:
    user_id: str = ""
    session_id: str = ""
    payment_id: str = ""


def current() -> Scope:
    return _scope.get() or Scope()


@contextmanager
def scope(user_id: str = "", session_id: str = "", payment_id: str = ""):
    token = _scope.set(Scope(user_id, session_id, payment_id))
    try:
        yield
    finally:
        _scope.reset(token)


@dataclass
class CallRecord:
    at: float
    op: str
    method: str
    path: str                  # template, e.g. /v2/checkout/orders/{id}/capture
    status: int | None         # None: no answer at all (timeout)
    ms: int
    debug_id: str
    mode: str
    write: bool                # a call that moves money or acts in the customer's name
    outcome: str               # ok | rejected | unknown
    user_id: str = ""
    session_id: str = ""
    payment_id: str = ""


class CallLog:
    MAX_PER_USER, MAX_USERS = 200, 2000

    def __init__(self, sink=None):
        self.sink = sink                    # audit log (or None)
        self._by_user: dict[str, deque] = {}
        self._lock = threading.Lock()

    def record(self, rec: CallRecord) -> None:
        with self._lock:
            if len(self._by_user) > self.MAX_USERS:
                self._by_user.pop(next(iter(self._by_user)))
            self._by_user.setdefault(rec.user_id, deque(maxlen=self.MAX_PER_USER)).append(rec)
        if self.sink:
            data = {k: v for k, v in asdict(rec).items() if k not in ("user_id", "session_id", "at")}
            try:
                self.sink.append("paypal.call", rec.session_id, data)
            except OSError:
                pass                        # the audit file failing must not change a payment's outcome

    def for_user(self, user_id: str, since: float = 0.0) -> list[dict]:
        with self._lock:
            rows = list(self._by_user.get(user_id, ()))
        return [asdict(r) for r in rows if r.at >= since]

    def money_moving_count(self, user_id: str, since: float = 0.0, payment_id: str = "") -> int:
        with self._lock:
            rows = list(self._by_user.get(user_id, ()))
        return sum(1 for r in rows if r.write and r.at >= since and (not payment_id or r.payment_id == payment_id))

    def clear_user(self, user_id: str) -> None:
        with self._lock:
            self._by_user.pop(user_id, None)
