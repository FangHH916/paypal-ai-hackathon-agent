"""The real client against a stub transport: token cache, 401 refresh, exact requests, no retries, error mapping, redaction."""
import json
import unittest

import requests

from app.paypal import calls, ids
from app.paypal.client import PayPalClient, payer_link
from app.paypal.errors import PayPalConfigError, PayPalError, PayPalUnknown, ReadOnlyViolation
from app.paypal.reader import PayPalReader, read_allowed, read_only_transport
from app.paypal.transport import PayPalTransport
from tests.paypal_helpers import StubAdapter, sandbox_cfg

DIGEST = "ab" * 32


def client(script=None, default=(200, {}), log=None):
    stub = StubAdapter(script, default)
    return PayPalClient(PayPalTransport(sandbox_cfg(), adapter=stub, log=log)), stub


def api_calls(stub):
    return [r for r in stub.seen if not r.url.endswith("/token")]


class TestToken(unittest.TestCase):
    def test_token_is_cached(self):
        c, stub = client()
        c.get_order("A")
        c.get_order("B")
        self.assertEqual(sum(r.url.endswith("/token") for r in stub.seen), 1)
        self.assertEqual(api_calls(stub)[0].headers["Authorization"], "Bearer tok1")

    def test_token_call_uses_basic_auth_and_client_credentials(self):
        c, stub = client()
        c.get_order("A")
        token = stub.seen[0]
        self.assertTrue(token.headers["Authorization"].startswith("Basic "))
        self.assertEqual(token.body, "grant_type=client_credentials")

    def test_token_expiry_refreshes(self):
        c, stub = client()
        c.get_order("A")
        c.t._expires_at = 0
        c.get_order("B")
        self.assertEqual(sum(r.url.endswith("/token") for r in stub.seen), 2)

    def test_401_refreshes_once_with_the_same_idempotency_key(self):
        c, stub = client([(401, {"error": "invalid_token"}), (201, {"batch_header": {}})])
        c.create_payout(DIGEST, amount_cents=500, currency="USD", receiver="a@b.example", note="n")
        posts = api_calls(stub)
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0].headers["PayPal-Request-Id"], posts[1].headers["PayPal-Request-Id"])
        self.assertEqual(posts[0].body, posts[1].body)
        self.assertEqual(posts[1].headers["Authorization"], "Bearer tok2")

    def test_401_twice_is_a_refusal_not_a_loop(self):
        c, stub = client([(401, {"error": "invalid_token"}), (401, {"error": "invalid_token"})])
        with self.assertRaises(PayPalError):
            c.get_order("A")
        self.assertEqual(len(api_calls(stub)), 2)


class TestExactRequests(unittest.TestCase):
    def test_payout(self):
        c, stub = client(default=(201, {"batch_header": {"payout_batch_id": "B"}}))
        c.create_payout(DIGEST, amount_cents=5050, currency="USD", receiver="anna@family.example", note="hi", subject="s")
        r = api_calls(stub)[0]
        self.assertEqual((r.method, r.url), ("POST", "https://api-m.sandbox.paypal.com/v1/payments/payouts"))
        self.assertEqual(r.headers["PayPal-Request-Id"], "star-" + DIGEST[:40])
        body = json.loads(r.body)
        self.assertEqual(body["sender_batch_header"]["sender_batch_id"], "star-" + DIGEST[:40])
        self.assertEqual(body["items"], [{"recipient_type": "EMAIL", "amount": {"currency": "USD", "value": "50.50"},
                                          "receiver": "anna@family.example", "note": "hi", "sender_item_id": DIGEST[:32]}])

    def test_order(self):
        c, stub = client(default=(201, {"id": "O", "status": "PAYER_ACTION_REQUIRED", "links": []}))
        c.create_order(DIGEST, reference_id="d_1", items=[{"name": "Monitor", "quantity": 2, "unit_cents": 3900}], currency="USD",
                       description="x", return_url="https://s.example/r", cancel_url="https://s.example/c")
        r = api_calls(stub)[0]
        self.assertEqual(r.url, "https://api-m.sandbox.paypal.com/v2/checkout/orders")
        self.assertEqual(r.headers["PayPal-Request-Id"], "ord-" + DIGEST[:40])
        self.assertEqual(r.headers["Prefer"], "return=representation")
        unit = json.loads(r.body)["purchase_units"][0]
        self.assertEqual(unit["amount"], {"currency_code": "USD", "value": "78.00", "breakdown": {"item_total": {"currency_code": "USD", "value": "78.00"}}})
        self.assertEqual(unit["custom_id"], DIGEST[:64])
        self.assertEqual(unit["items"][0]["quantity"], "2")
        ctx = json.loads(r.body)["payment_source"]["paypal"]["experience_context"]
        self.assertEqual((ctx["user_action"], ctx["shipping_preference"]), ("PAY_NOW", "NO_SHIPPING"))

    def test_capture_and_refund_keys(self):
        c, stub = client(default=(201, {"id": "X"}))
        c.capture_order("O", DIGEST)
        c.refund_capture("C", DIGEST)
        c.refund_capture("C", DIGEST, amount_cents=100, currency="USD")
        cap, full, part = api_calls(stub)
        self.assertEqual(cap.url.rsplit("/", 2)[1:], ["O", "capture"])
        self.assertEqual(cap.headers["PayPal-Request-Id"], "cap-" + DIGEST[:40])
        self.assertEqual(full.headers["PayPal-Request-Id"], "ref-" + DIGEST[:40])
        self.assertEqual(json.loads(full.body), {})          # spec: an empty body means a full refund
        self.assertEqual(json.loads(part.body)["amount"], {"currency_code": "USD", "value": "1.00"})

    def test_invoice_number_plays_the_idempotency_role(self):
        c, stub = client(default=(201, {"id": "I"}))
        c.create_invoice(number=ids.invoice_number(DIGEST), currency="USD", recipient_email="d@f.example", item_name="x", amount_cents=4000)
        body = json.loads(api_calls(stub)[0].body)
        self.assertEqual(body["detail"]["invoice_number"], "STAR-" + DIGEST[:16])
        self.assertNotIn("PayPal-Request-Id", api_calls(stub)[0].headers)

    def test_webhook_verify_splices_the_raw_event_byte_for_byte(self):
        c, stub = client(default=(200, {"verification_status": "SUCCESS"}))
        raw = b'{"id":"WH-1", "event_type":"X",   "resource": {"a": 1.10}}'        # spacing and number form kept
        c.verify_webhook_signature(raw_event=raw, webhook_id="WID", headers={
            "paypal-auth-algo": "A", "paypal-cert-url": "https://c", "paypal-transmission-id": "T",
            "paypal-transmission-sig": "S", "paypal-transmission-time": "W"})
        body = api_calls(stub)[0].body
        self.assertIn(b'"webhook_event":' + raw + b"}", body)
        self.assertEqual(json.loads(body)["webhook_id"], "WID")

    def test_payer_link(self):
        self.assertEqual(payer_link({"links": [{"rel": "self", "href": "s"}, {"rel": "payer-action", "href": "p"}]}), "p")
        self.assertEqual(payer_link({"links": [{"rel": "approve", "href": "a"}]}), "a")
        self.assertEqual(payer_link({}), "")


class TestFailureSemantics(unittest.TestCase):
    def test_timeout_is_unknown_and_never_retried(self):
        c, stub = client([requests.exceptions.ReadTimeout("slow")])
        with self.assertRaises(PayPalUnknown):
            c.create_payout(DIGEST, amount_cents=500, currency="USD", receiver="a@b.example", note="n")
        self.assertEqual(len(api_calls(stub)), 1)

    def test_connection_reset_is_unknown(self):
        c, _ = client([requests.exceptions.ConnectionError("reset")])
        with self.assertRaises(PayPalUnknown):
            c.capture_order("O", DIGEST)

    def test_5xx_on_a_write_is_unknown_and_not_retried(self):
        c, stub = client([(503, {"name": "SERVICE_UNAVAILABLE"})])
        with self.assertRaises(PayPalUnknown) as e:
            c.refund_capture("C", DIGEST)
        self.assertEqual(e.exception.status, 503)
        self.assertEqual(len(api_calls(stub)), 1)

    def test_4xx_is_a_definite_refusal_with_details(self):
        c, _ = client([(422, {"name": "UNPROCESSABLE_ENTITY", "message": "m", "debug_id": "D", "details": [{"issue": "INSTRUMENT_DECLINED"}]})])
        with self.assertRaises(PayPalError) as e:
            c.capture_order("O", DIGEST)
        self.assertTrue(e.exception.has_issue("INSTRUMENT_DECLINED"))
        self.assertEqual(e.exception.debug_id, "D")

    def test_unreadable_success_is_unknown(self):
        c, _ = client([(201, b"<html>")])
        with self.assertRaises(PayPalUnknown):
            c.capture_order("O", DIGEST)

    def test_no_redirects_followed(self):
        c, stub = client([(302, {})])
        c.get_order("O")
        self.assertEqual(len(api_calls(stub)), 1)


class TestRedaction(unittest.TestCase):
    def test_log_holds_no_secret_id_or_email(self):
        sink = []

        class Sink:
            def append(self, event, session_id, data):
                sink.append((event, session_id, data))
        log = calls.CallLog(Sink())
        c, _ = client(default=(201, {"batch_header": {"payout_batch_id": "BATCHSECRETID"}}), log=log)
        with calls.scope("u", "s", "p"):
            c.create_payout(DIGEST, amount_cents=500, currency="USD", receiver="anna@family.example", note="n")
        text = json.dumps(sink) + json.dumps(log.for_user("u"))
        for secret in ("secret-secret-value", "id-secret-value", "tok1", "anna@family.example", "Bearer", "Basic ", "BATCHSECRETID"):
            self.assertNotIn(secret, text)
        self.assertEqual(sink[-1][0], "paypal.call")
        self.assertEqual(sink[-1][2]["path"], "/v1/payments/payouts")
        self.assertEqual(log.money_moving_count("u"), 1)

    def test_token_calls_are_not_money_moving(self):
        log = calls.CallLog()
        c, _ = client(log=log)
        with calls.scope("u"):
            c.get_order("O")
        self.assertEqual(log.money_moving_count("u"), 0)


class TestReaderAllowlist(unittest.TestCase):
    def reader(self):
        stub = StubAdapter(default=(200, {}))
        return PayPalReader(read_only_transport(sandbox_cfg(), adapter=stub)), stub

    def test_reads_work(self):
        r, stub = self.reader()
        r.get_order("A")
        r.search_invoices({"reference": "x"})
        r.list_disputes()
        self.assertEqual(len(api_calls(stub)), 3)

    def test_writes_never_reach_the_transport(self):
        r, stub = self.reader()
        for method, path in (("POST", "/v1/payments/payouts"), ("POST", "/v2/checkout/orders"), ("POST", "/v2/checkout/orders/A/capture"),
                             ("POST", "/v2/payments/captures/A/refund"), ("POST", "/v2/invoicing/invoices"), ("POST", "/v2/invoicing/invoices/A/send"),
                             ("POST", "/v1/payments/payouts-item/A/cancel"), ("POST", "/v2/invoicing/invoices/A/payments"), ("DELETE", "/v2/invoicing/invoices/A")):
            self.assertFalse(read_allowed(method, path), path)
            with self.assertRaises(ReadOnlyViolation):
                r.t.request("x", method, path)
        self.assertEqual(api_calls(stub), [])

    def test_reader_has_no_write_methods(self):
        for name in ("create_payout", "create_order", "capture_order", "refund_capture", "cancel_payout_item", "create_invoice", "send_invoice"):
            self.assertFalse(hasattr(PayPalReader, name))
            self.assertTrue(hasattr(PayPalClient, name))


class TestIds(unittest.TestCase):
    def test_keys_are_pure_functions_of_the_digest(self):
        self.assertEqual(ids.key_for("payouts", DIGEST), ids.key_for("payouts", DIGEST))
        self.assertNotEqual(ids.key_for("payouts", DIGEST), ids.key_for("payouts", "cd" * 32))
        self.assertLessEqual(len(ids.invoice_number(DIGEST)), 25)
        self.assertLessEqual(len(ids.sender_item_id(DIGEST)), 63)
        self.assertLessEqual(len(ids.order_key(DIGEST)), 108)

    def test_config_error_never_reaches_the_network(self):
        from app.paypal.config import PayPalConfig
        stub = StubAdapter()
        c = PayPalClient(PayPalTransport(PayPalConfig(mode="sandbox", error="no keys"), adapter=stub))
        with self.assertRaises(PayPalConfigError):
            c.get_order("A")
        self.assertEqual(stub.seen, [])


if __name__ == "__main__":
    unittest.main()
