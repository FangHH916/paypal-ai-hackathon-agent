"""The PayPal routes over real HTTP: a redirect sets nothing, capture never happens on a GET, webhooks need a signature,
a concurrent double click makes one payment, simulator routes vanish outside simulator mode."""
import base64
import json
import os
import socket
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

os.environ.setdefault("RULE_FIXED_HOUR", "14")

import requests  # noqa: E402
import uvicorn  # noqa: E402

from app import runtime  # noqa: E402
from app.kernel import p256  # noqa: E402
from app.server import app  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.base = f"http://localhost:{cls.port}"
        cls.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cls.port, log_level="warning"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            try:
                requests.get(cls.base + "/api/health", timeout=0.5)
                break
            except requests.ConnectionError:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=5)

    def setUp(self):
        self.sim = runtime.execution_gateway.simulator
        self.old_settle = self.sim.settle_seconds
        self.sim.settle_seconds = 0
        self.addCleanup(lambda: setattr(self.sim, "settle_seconds", self.old_settle))
        self.sid = self.post("/api/reset", {"visitor": "abababababababab", "world": True}).json()["session_id"]
        self.key, jwk = p256.generate_keypair()
        self.post("/api/key", {"session_id": self.sid, "public_key": jwk})

    def post(self, path, body=None, **kw):
        return requests.post(self.base + path, json=body if body is not None else {}, timeout=15, **kw)

    def get(self, path, **params):
        return requests.get(self.base + path, params=params, timeout=15)

    def card(self, text):
        return self.post("/api/chat", {"session_id": self.sid, "text": text}).json()["ui"]["card"]

    def confirm_body(self, card):
        body = {"session_id": self.sid, "draft_id": card["draft_id"], "digest": card["digest"]}
        ch = self.post("/api/challenge", body).json()["payload"]
        return {**body, "assertion": {"payload": ch, "signature": base64.b64encode(p256.sign(self.key, ch.encode())).decode()}}

    def purchase(self):
        r = self.post("/api/confirm", self.confirm_body(self.card("Get me a blood pressure monitor")))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class TestRedirectsSetNothing(Base):
    def test_get_on_the_return_link_never_captures(self):
        res = self.purchase()
        pid = res["payment"]["payment_id"]
        order = res["payment"]["paypal"]["order_id"]
        self.sim.buyer_decide_order(order, "approve")
        r = requests.get(f"{self.base}/?pp_return=1&payment={pid}&status=COMPLETED&paid=true", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.sim.st["orders"][order]["status"], "APPROVED")      # still not captured
        self.assertEqual(runtime.wallet.payment(pid)["state"], "AWAITING_APPROVAL")

    def test_return_post_ignores_what_the_client_claims(self):
        res = self.purchase()
        r = self.post("/api/paypal/orders/return", {"session_id": self.sid, "payment_id": res["payment"]["payment_id"], "status": "COMPLETED", "paid": True})
        self.assertEqual(r.json()["payment"]["state"], "AWAITING_APPROVAL")
        self.assertFalse(r.json()["payment"]["verified"])

    def test_return_is_owner_only(self):
        res = self.purchase()
        other = self.post("/api/reset", {"visitor": "cdcdcdcdcdcdcdcd"}).json()["session_id"]
        r = self.post("/api/paypal/orders/return", {"session_id": other, "payment_id": res["payment"]["payment_id"]})
        self.assertEqual(r.status_code, 400)

    def test_the_whole_way_through_and_cancel(self):
        res = self.purchase()
        self.sim.buyer_decide_order(res["payment"]["paypal"]["order_id"], "approve")
        back = self.post("/api/paypal/orders/return", {"session_id": self.sid, "payment_id": res["payment"]["payment_id"]}).json()
        self.assertEqual((back["payment"]["state"], back["payment"]["verified"]), ("PAID", True))
        self.assertIn("The simulator confirms", back["reply"])
        res2 = self.post("/api/confirm", self.confirm_body(self.card("I need a weekly groceries box"))).json()
        out = self.post("/api/paypal/orders/cancelled", {"session_id": self.sid, "payment_id": res2["payment"]["payment_id"]}).json()
        self.assertEqual(out["payment"]["state"], "BUYER_CANCELLED")

    def test_loopback_redirect_keeps_the_return_query(self):
        r = requests.get(f"http://127.0.0.1:{self.port}/?pp_return=1&payment=pay_x", allow_redirects=False, timeout=5)
        self.assertEqual(r.headers["location"], f"http://localhost:{self.port}/?pp_return=1&payment=pay_x")


class TestDoubleClick(Base):
    def test_concurrent_confirms_make_one_payout(self):
        before = len(self.sim.st["batches"])
        body = self.confirm_body(self.card("Send 50 to my daughter"))
        with ThreadPoolExecutor(4) as pool:
            rs = list(pool.map(lambda _: self.post("/api/confirm", body), range(4)))
        ok = [r.json() for r in rs if r.status_code == 200]
        self.assertGreaterEqual(len(ok), 1)
        self.assertEqual({r["payment"]["payment_id"] for r in ok}, {ok[0]["payment"]["payment_id"]})
        self.assertEqual(len(self.sim.st["batches"]), before + 1)

    def test_retry_after_a_lost_reply_returns_unknown_and_tells_her_not_to_pay_again(self):
        uid = runtime.sessions.get(self.sid).user_id
        self.sim.arm(uid, "timeout_after_commit", op="payout_create")
        r = self.post("/api/confirm", self.confirm_body(self.card("Send 50 to my daughter"))).json()
        self.assertEqual(r["payment"]["state"], "UNKNOWN")
        self.assertIn("don't pay again", r["reply"])
        rows = self.get("/api/payments", session_id=self.sid).json()["payments"]    # polling reconciles
        self.assertEqual((rows[0]["state"], rows[0]["verified"]), ("SENT", True))


class TestWebhookRoute(Base):
    def test_no_headers_400(self):
        self.assertEqual(self.post("/api/paypal/webhook", {"id": "x", "event_type": "y"}).json()["code"], "MISSING_HEADERS")

    def test_forged_400_real_200(self):
        res = self.post("/api/confirm", self.confirm_body(self.card("Send 50 to my daughter")))
        self.assertEqual(res.status_code, 200)
        event = {"id": "WH-HTTP", "event_type": "CUSTOMER.DISPUTE.UPDATED", "resource": {"dispute_id": "PP-1"}}
        raw = json.dumps(event, sort_keys=True, separators=(",", ":"))
        good = self.sim._sign(event)
        hdr = {"Content-Type": "application/json"}
        bad = requests.post(self.base + "/api/paypal/webhook", data=raw, headers={**hdr, **good, "paypal-transmission-sig": "Zm9yZ2Vk"}, timeout=5)
        self.assertEqual((bad.status_code, bad.json()["code"]), (400, "BAD_SIGNATURE"))
        ok = requests.post(self.base + "/api/paypal/webhook", data=raw, headers={**hdr, **good}, timeout=5)
        self.assertEqual((ok.status_code, ok.json()["code"]), (200, "OK"))
        again = requests.post(self.base + "/api/paypal/webhook", data=raw, headers={**hdr, **good}, timeout=5)
        self.assertEqual((again.status_code, again.json()["code"]), (200, "DUPLICATE"))

    def test_size_limit(self):
        r = requests.post(self.base + "/api/paypal/webhook", data="x" * 70_000, headers={"Content-Type": "application/json"}, timeout=5)
        self.assertEqual(r.status_code, 413)

    def test_browser_cross_site_refused(self):
        self.assertEqual(self.post("/api/paypal/webhook", {}, headers={"Origin": "https://evil.example"}).status_code, 403)


class TestPanelsAndModes(Base):
    def test_ticker_counts_from_the_server_log(self):
        self.assertEqual(self.get("/api/paypal/calls", session_id=self.sid).json()["money_moving_this_task"], 0)
        self.post("/api/confirm", self.confirm_body(self.card("Send 50 to my daughter")))
        log = self.get("/api/paypal/calls", session_id=self.sid).json()
        self.assertEqual(log["money_moving_total"], 1)
        self.assertEqual(log["mode_label"], "SIMULATED")
        self.assertTrue(any(c["path"] == "/v1/payments/payouts" and c["status"] == 201 for c in log["calls"]))
        self.assertNotIn("anna", json.dumps(log))

    def test_panels(self):
        offers = self.get("/api/catalogue", session_id=self.sid).json()["offers"]
        self.assertEqual({o["verdict"] for o in offers if o["name"] == "Blood pressure monitor"}, {"ok", "stop"})
        self.assertNotIn("approve the payment", json.dumps(offers))
        self.assertEqual(len(self.get("/api/invoices", session_id=self.sid).json()["invoices"]), 2)
        self.assertTrue(self.get("/api/disputes", session_id=self.sid).json()["available"])

    def test_health_says_what_is_real(self):
        h = self.get("/api/health").json()
        self.assertEqual(h["paypal"]["label"], "SIMULATED")
        self.assertIn("Offline rule engine", h["engine"])

    def test_simulator_routes_vanish_outside_simulator_mode(self):
        with patch.object(runtime.execution_gateway, "simulator", None):
            self.assertEqual(self.get("/simulator/checkout").status_code, 404)
            self.assertEqual(self.get("/simulator/api/info", token="x").status_code, 404)
            self.assertEqual(self.post("/simulator/api/decide", {"kind": "order", "id": "x", "decision": "approve"}).status_code, 404)
            self.assertEqual(self.post("/api/lab/drill").status_code, 404)
            self.assertEqual(self.post("/api/lab/naive-agent").status_code, 404)

    def test_page_csp_keeps_scripts_strict_and_has_a_style_nonce(self):
        csp = requests.get(self.base + "/", timeout=5).headers["content-security-policy"]
        self.assertIn("script-src 'self';", csp)
        self.assertRegex(csp, r"style-src 'self' 'nonce-[\w-]+'")
        self.assertNotIn("unsafe-inline", csp)

    def test_drill_and_comparison(self):
        d = self.post("/api/lab/drill").json()
        self.assertTrue(d["passed"])
        self.assertEqual(d["payouts_that_exist"], 1)
        n = self.post("/api/lab/naive-agent").json()
        self.assertIn("not a test of any real product", n["disclaimer"])
        self.assertEqual(n["star"]["money_moving_calls"], 0)

    def test_old_identity_routes_are_gone(self):
        self.assertEqual(self.post("/api/review/secondary", {}).status_code, 404)
