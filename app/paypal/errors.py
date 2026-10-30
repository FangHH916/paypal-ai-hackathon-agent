"""What can go wrong on a PayPal call. The split matters more than the details:

  PayPalError        PayPal answered and refused. Definite: nothing happened, the money did not move.
  PayPalUnknown      No trustworthy answer (timeout, reset, 5xx, unreadable body). The outcome is UNKNOWN: it must be
                     reconciled by a query, and never retried with a new idempotency key.
  PayPalConfigError  This server can't talk to PayPal at all (no credentials, unsupported mode). Nothing was sent.
"""
from __future__ import annotations


class PayPalConfigError(Exception):
    pass


class PayPalError(Exception):
    def __init__(self, status: int, name: str = "", message: str = "", debug_id: str = "",
                 links: list | None = None, details: list | None = None):
        super().__init__(f"PayPal {status} {name}: {message}".strip())
        self.status, self.name, self.message = status, name, message
        self.debug_id, self.links, self.details = debug_id, links or [], details or []

    def has_issue(self, issue: str) -> bool:
        """PayPal puts the specific reason in details[].issue (Orders) or in the top-level name."""
        return self.name == issue or any(isinstance(d, dict) and d.get("issue") == issue for d in self.details)


class PayPalUnknown(Exception):
    def __init__(self, reason: str, status: int | None = None, debug_id: str = ""):
        super().__init__(reason)
        self.reason, self.status, self.debug_id = reason, status, debug_id


class ReadOnlyViolation(Exception):
    """The reader's transport refused a request that isn't on its read allowlist. Raised before any socket opens."""
