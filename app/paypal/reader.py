"""PayPalReader: read-only PayPal calls. This is all the agent's tools ever hold (invariant I1).

Its transport has an allowlist that sits below the client code: a GET on a named read path, the search-invoices POST,
and the token call. Anything else raises ReadOnlyViolation before a socket opens. So even a bug or an injected
instruction that reached this object could not move money. This module must never import client.py.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from requests.adapters import HTTPAdapter

from app.paypal.errors import ReadOnlyViolation
from app.paypal.transport import PayPalTransport

_READ_GET = [re.compile(p) for p in (
    r"/v1/payments/payouts/[^/]+", r"/v1/payments/payouts-item/[^/]+", r"/v2/checkout/orders/[^/]+",
    r"/v2/payments/captures/[^/]+", r"/v2/payments/refunds/[^/]+", r"/v2/invoicing/invoices/[^/]+",
    r"/v1/customer/disputes", r"/v1/customer/disputes/[^/]+", r"/v1/reporting/transactions",
    r"/v1/notifications/webhooks")]
_READ_POST = {"/v1/oauth2/token", "/v2/invoicing/search-invoices"}


def read_allowed(method: str, path: str) -> bool:
    if method == "GET":
        return any(rx.fullmatch(path) for rx in _READ_GET)
    return method == "POST" and path in _READ_POST


class AllowlistAdapter(HTTPAdapter):
    """Wraps the real transport adapter. The check runs on the final prepared request, right before sending."""

    def __init__(self, inner: HTTPAdapter):
        super().__init__()
        self.inner = inner

    def send(self, request, **kw):
        path = urlsplit(request.url).path
        if not read_allowed(request.method, path):
            raise ReadOnlyViolation(f"{request.method} {path} is not a read call")
        return self.inner.send(request, **kw)

    def close(self):
        self.inner.close()


def read_only_transport(cfg, adapter=None, log=None) -> PayPalTransport:
    return PayPalTransport(cfg, adapter=AllowlistAdapter(adapter or HTTPAdapter(max_retries=0)), log=log)


class PayPalReader:
    def __init__(self, transport: PayPalTransport):
        self.t = transport

    @property
    def cfg(self):
        return self.t.cfg

    # ---------------------------------------------------------------- Payouts
    def get_payout_batch(self, batch_id: str) -> dict:
        return self.t.request("payout_batch_get", "GET", "/v1/payments/payouts/{id}",
                              path=f"/v1/payments/payouts/{batch_id}")

    def get_payout_item(self, item_id: str) -> dict:
        return self.t.request("payout_item_get", "GET", "/v1/payments/payouts-item/{id}",
                              path=f"/v1/payments/payouts-item/{item_id}")

    # ---------------------------------------------------------------- Orders and Payments
    def get_order(self, order_id: str) -> dict:
        return self.t.request("order_get", "GET", "/v2/checkout/orders/{id}", path=f"/v2/checkout/orders/{order_id}")

    def get_capture(self, capture_id: str) -> dict:
        return self.t.request("capture_get", "GET", "/v2/payments/captures/{id}",
                              path=f"/v2/payments/captures/{capture_id}")

    def get_refund(self, refund_id: str) -> dict:
        return self.t.request("refund_get", "GET", "/v2/payments/refunds/{id}",
                              path=f"/v2/payments/refunds/{refund_id}")

    # ---------------------------------------------------------------- Invoicing
    def get_invoice(self, invoice_id: str) -> dict:
        return self.t.request("invoice_get", "GET", "/v2/invoicing/invoices/{id}",
                              path=f"/v2/invoicing/invoices/{invoice_id}")

    def search_invoices(self, filters: dict, page_size: int = 50) -> dict:
        """POST, but a read: spec operation search-invoices. `filters` uses the spec's search_data field names."""
        return self.t.request("invoice_search", "POST", "/v2/invoicing/search-invoices", body=filters,
                              params={"page": 1, "page_size": page_size, "total_required": "true"})

    # ---------------------------------------------------------------- Disputes, Reporting, Webhooks
    def list_disputes(self, page_size: int = 20) -> dict:
        return self.t.request("disputes_list", "GET", "/v1/customer/disputes", params={"page_size": page_size})

    def get_dispute(self, dispute_id: str) -> dict:
        return self.t.request("dispute_get", "GET", "/v1/customer/disputes/{id}",
                              path=f"/v1/customer/disputes/{dispute_id}")

    def list_transactions(self, start_date: str, end_date: str, page_size: int = 50) -> dict:
        return self.t.request("transactions_list", "GET", "/v1/reporting/transactions",
                              params={"start_date": start_date, "end_date": end_date, "page_size": page_size,
                                      "fields": "transaction_info"})

    def list_webhooks(self) -> dict:
        return self.t.request("webhooks_list", "GET", "/v1/notifications/webhooks")
