"""Every request the client makes and every response the simulator gives, checked against PayPal's official OpenAPI files
(a subset built by scripts/build_paypal_subset.py). Field names, required fields, enums and length limits.

This is why a shape mistake shows up here rather than on the first real sandbox call. It does not replace that call:
behaviour the specs don't describe is listed as unverified in docs/PAYPAL_NOTES.md.
"""
import json
from urllib.parse import parse_qs, urlsplit

from unittest.mock import patch

from app.config import settings
from tests.paypal_helpers import World, check, find_operation, request_problems, response_schema


class Recording(World):
    """Runs the whole set of flows, recording each request and each response the simulator answered with."""

    def setUp(self):
        super().setUp()
        limit = patch.object(settings, "velocity_max", 20)
        limit.start()
        self.addCleanup(limit.stop)
        self.responses = []
        original = self.sim.handle

        def spy(request):
            r = original(request)
            self.responses.append((request, r))
            return r
        self.sim.handle = spy

    def run_all_flows(self):
        self.turn("Send 50 to my daughter")
        self.sign()
        rec = self.buy()
        self.turn("I want my money back")
        self.sign()
        self.turn("Ask my son to pay me back 40 dollars")
        self.sign()
        self.turn("Pay the power bill")
        self.sign()
        self.wallet.add_payee("u_lee", "Tom Reed", "tom.reed@mail.example", "friend")
        self.turn("Send 20 to Tom Reed")
        unclaimed = self.rec(self.sign())
        self.assertEqual(unclaimed["state"], "UNCLAIMED")
        self.turn("Stop that payment")
        self.sign()
        self.tools.call(self.s, "list_disputes", {})
        self.gateway.reader.list_transactions("2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z")
        self.gateway.reader.list_webhooks()
        self.gateway.reader.get_capture(rec["paypal"]["capture_id"])
        self.gateway.reader.get_dispute("PP-D-SIM-0001")
        self.pump()


class TestAgainstTheSpec(Recording):
    def test_every_request_matches_the_spec(self):
        self.run_all_flows()
        problems, tried = [], set()
        for request, _ in self.responses:
            u = urlsplit(request.url)
            if u.path == "/v1/oauth2/token":
                continue
            found = find_operation(request.method, u.path)
            self.assertIsNotNone(found, f"{request.method} {u.path}")
            tried.add((request.method, found[2]))
            body = request.body or b""
            body = body.encode() if isinstance(body, str) else body
            problems += request_problems(request.method, u.path, body, dict(request.headers), parse_qs(u.query))
        self.assertEqual(problems, [])
        for op in (("POST", "/v1/payments/payouts"), ("POST", "/v2/checkout/orders"), ("POST", "/v2/checkout/orders/{id}/capture"),
                   ("POST", "/v2/payments/captures/{capture_id}/refund"), ("GET", "/v2/payments/refunds/{refund_id}"),
                   ("POST", "/v2/invoicing/invoices"), ("POST", "/v2/invoicing/invoices/{invoice_id}/send"),
                   ("POST", "/v2/invoicing/search-invoices"), ("POST", "/v1/notifications/verify-webhook-signature"),
                   ("GET", "/v1/customer/disputes"), ("GET", "/v1/reporting/transactions"),
                   ("POST", "/v1/payments/payouts-item/{payout_item_id}/cancel")):
            self.assertIn(op, tried)

    def test_every_simulator_response_matches_the_spec(self):
        self.run_all_flows()
        problems = []
        for request, r in self.responses:
            u = urlsplit(request.url)
            found = find_operation(request.method, u.path)
            if not found or r.status_code >= 400:
                continue
            spec, op, template = found
            schema = response_schema(spec, op, r.status_code)
            self.assertIsNotNone(schema, f"{request.method} {template} has no {r.status_code} in the spec")
            check(spec, schema, json.loads(r.content), f"{request.method} {template} {r.status_code}", problems, strict_required=False)
        self.assertEqual(problems, [])
