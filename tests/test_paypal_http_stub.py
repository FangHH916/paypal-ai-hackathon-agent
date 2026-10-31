"""The real client over a real socket, against a local HTTP server: real timeouts, real 5xx, real malformed bodies."""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.paypal.client import PayPalClient
from app.paypal.config import PayPalConfig
from app.paypal.errors import PayPalError, PayPalUnknown
from app.paypal.transport import PayPalTransport


class Handler(BaseHTTPRequestHandler):
    plan: dict = {}
    seen: list = []

    def log_message(self, *a):
        pass

    def _go(self):
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        Handler.seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
        if self.path.startswith("/v1/oauth2/token"):
            return self._send(200, json.dumps({"access_token": "real-socket-token", "expires_in": 3600}).encode())
        action = Handler.plan.get(self.path.split("?")[0], (200, b"{}"))
        if action == "sleep":
            time.sleep(1.5)
            return self._send(200, b"{}")
        self._send(*action)

    def _send(self, status, data):
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("paypal-debug-id", "socket-dbg")
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass

    do_GET = do_POST = _go


class LocalConfig(PayPalConfig):
    @property
    def base_url(self):
        return self.__dict__["_base"]


class TestOverARealSocket(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.plan, Handler.seen = {}, []
        cfg = LocalConfig(mode="sandbox", client_id="id", client_secret="secret", timeout=0.4, connect_timeout=0.5)
        object.__setattr__(cfg, "_base", f"http://127.0.0.1:{self.server.server_address[1]}")
        self.client = PayPalClient(PayPalTransport(cfg))

    def posts(self, path):
        return [r for r in Handler.seen if r["path"].startswith(path)]

    def test_success_with_token_and_key_on_the_wire(self):
        Handler.plan["/v1/payments/payouts"] = (201, json.dumps({"batch_header": {"payout_batch_id": "B1"}}).encode())
        r = self.client.create_payout("ab" * 32, amount_cents=100, currency="USD", receiver="a@b.example", note="n")
        self.assertEqual(r["batch_header"]["payout_batch_id"], "B1")
        sent = self.posts("/v1/payments/payouts")[0]
        self.assertEqual(sent["headers"]["Authorization"], "Bearer real-socket-token")
        self.assertEqual(sent["headers"]["PayPal-Request-Id"], "star-" + ("ab" * 32)[:40])

    def test_a_true_socket_read_timeout_is_unknown_and_not_retried(self):
        Handler.plan["/v2/checkout/orders/O/capture"] = "sleep"
        t0 = time.time()
        with self.assertRaises(PayPalUnknown):
            self.client.capture_order("O", "ab" * 32)
        self.assertLess(time.time() - t0, 1.2)
        self.assertEqual(len(self.posts("/v2/checkout/orders/O/capture")), 1)

    def test_connection_refused_is_unknown(self):
        cfg = LocalConfig(mode="sandbox", client_id="id", client_secret="secret", timeout=0.2, connect_timeout=0.2)
        object.__setattr__(cfg, "_base", "http://127.0.0.1:9")
        with self.assertRaises(PayPalUnknown):
            PayPalClient(PayPalTransport(cfg)).get_order("O")

    def test_500_is_unknown(self):
        Handler.plan["/v2/payments/captures/C/refund"] = (500, b'{"name":"INTERNAL_SERVER_ERROR"}')
        with self.assertRaises(PayPalUnknown) as e:
            self.client.refund_capture("C", "ab" * 32)
        self.assertEqual((e.exception.status, e.exception.debug_id), (500, "socket-dbg"))
        self.assertEqual(len(self.posts("/v2/payments/captures/C/refund")), 1)

    def test_malformed_json_on_success_is_unknown(self):
        Handler.plan["/v2/checkout/orders/O"] = (200, b"not json at all")
        with self.assertRaises(PayPalUnknown):
            self.client.get_order("O")

    def test_malformed_json_on_an_error_is_still_a_refusal(self):
        Handler.plan["/v2/checkout/orders/O"] = (400, b"<html>bad</html>")
        with self.assertRaises(PayPalError) as e:
            self.client.get_order("O")
        self.assertEqual((e.exception.status, e.exception.debug_id), (400, "socket-dbg"))

    def test_empty_204_is_fine(self):
        Handler.plan["/v2/checkout/orders/O"] = (204, b"")
        self.assertEqual(self.client.get_order("O"), {"_status": 204})
