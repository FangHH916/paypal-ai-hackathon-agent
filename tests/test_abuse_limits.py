"""Regressions from the code review: one bill paid once, resets and anonymous clients on a shared demo, rate-limit identity."""
import json
import os
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

os.environ.setdefault("RULE_FIXED_HOUR", "14")     # before anything imports app.config: results can't depend on the clock

from app import runtime, security  # noqa: E402
from app.config import ROOT, settings  # noqa: E402
from app.kernel.state import SessionStore, UnknownSession  # noqa: E402
from app.models import Phase  # noqa: E402
from app.wallet import catalogue, payments  # noqa: E402
from app.wallet.guardian import GuardianError  # noqa: E402
from app.wallet.ledger import DEMO_USER, Wallet  # noqa: E402
from tests.paypal_helpers import USER, World  # noqa: E402
from tests.test_paypal_http import Base as HttpBase  # noqa: E402


def bill_record(n, invoice, cents=None):
    digest = f"{n:x}".rjust(64, "0")
    cents = invoice["amount_cents"] if cents is None else cents
    card = {"draft_id": f"d{n}", "kind": "bill", "currency": "USD", "amount_cents": cents, "payee_name": invoice["invoicer_name"],
            "plan_id": "", "payee_id": invoice["payee_id"], "invoice_id": invoice["invoice_id"]}
    return payments.new_record(payment_id=f"pay_{n}", user_id=DEMO_USER, session_id="s", card=card, digest=digest, rail="invoice",
                               idempotency_key=f"key{n}", authorization=None, verification=None, decision=None)


class TestOneBillIsPaidOnce(World):
    def invoice(self):
        return self.wallet.invoice(USER, catalogue.INVOICE_SEEDS[0]["invoice_id"])

    def test_parallel_records_for_one_invoice_reserve_once(self):
        """Plenty of allowance, so only the invoice check can refuse: this is the check the old bill test relied on."""
        inv, start = self.invoice(), self.wallet.allowance_left(USER)

        def post(n):
            try:
                return self.wallet.begin_payment(bill_record(n, inv), reserve=True)["state"]
            except ValueError as e:
                self.assertIn("R62", str(e))
                return "REJECTED"
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(post, (1, 2)))
        self.assertEqual(sorted(results), ["REJECTED", "SUBMITTING"])
        self.assertEqual(self.wallet.allowance_left(USER), start - inv["amount_cents"])

    def test_a_paid_or_cancelled_invoice_is_refused(self):
        inv = self.invoice()
        for status in ("PAID", "CANCELLED"):
            inv["status"] = status
            with self.assertRaises(ValueError):
                self.wallet.begin_payment(bill_record(3, inv), reserve=True)

    def test_a_different_amount_from_the_invoice_is_refused(self):
        inv = self.invoice()
        with self.assertRaises(ValueError):
            self.wallet.begin_payment(bill_record(4, inv, inv["amount_cents"] + 1), reserve=True)

    def test_a_failed_attempt_frees_the_bill_again(self):
        inv = self.invoice()
        self.wallet.begin_payment(bill_record(5, inv), reserve=True)
        self.wallet.update_payment("pay_5", "FAILED", source="test")
        self.assertEqual(self.wallet.begin_payment(bill_record(6, inv), reserve=True)["state"], "SUBMITTING")

    def test_asking_twice_stops_the_second_card_with_a_plain_reason(self):
        self.turn("Pay my power bill")
        first = self.s.current_draft
        self.sign(first)
        left = self.wallet.allowance_left(USER)
        self.turn("Pay my power bill")
        second = self.s.current_draft
        if second and second.draft_id != first.draft_id:      # the offline model may decline to draft it at all
            self.assertEqual(self.s.phase, Phase.BLOCKED)
            self.assertIn("R62_BILL_IN_PROGRESS", second.risk.rule_hits)
        self.assertEqual(self.wallet.allowance_left(USER), left)
        self.assertEqual(len([r for r in self.wallet.payments_for(USER) if r["kind"] == "bill"]), 1)


class TestVisitorCap(unittest.TestCase):
    def test_reset_user_cannot_grow_past_the_cap(self):
        w = Wallet()
        with patch.object(Wallet, "MAX_VISITORS", 3):
            for i in range(6):
                w.reset_user(Wallet.visitor_user_id(f"v{i}"))
        self.assertLessEqual(len([u for u in w.users() if "~" in u]), 3)

    def test_at_the_cap_a_frozen_or_guarded_world_is_kept_and_the_idlest_goes(self):
        w = Wallet()
        with patch.object(Wallet, "MAX_VISITORS", 3):
            ids = [Wallet.visitor_user_id(f"v{i}") for i in range(3)]
            for uid in ids:
                w.user(uid)
                time.sleep(0.002)
            w.set_frozen(ids[0], True, "the customer")       # the oldest visitor froze their payments
            w.user(ids[1])                                   # ...the second is active again
            w.user(Wallet.visitor_user_id("new"))
        self.assertIn(ids[0], w.users())
        self.assertTrue(w.users()[ids[0]]["frozen"]["on"])
        self.assertIn(ids[1], w.users())
        self.assertNotIn(ids[2], w.users())

    def test_an_evicted_visitor_leaves_no_payment_records_behind(self):
        w = Wallet()
        with patch.object(Wallet, "MAX_VISITORS", 1):
            uid = Wallet.visitor_user_id("old")
            inv = w.invoice(uid, catalogue.INVOICE_SEEDS[0]["invoice_id"])
            rec = bill_record(9, inv)
            rec["user_id"] = uid
            w.begin_payment(rec, reserve=True)
            w.user(Wallet.visitor_user_id("new"))
        self.assertEqual(w.payments, {})


class TestSessionStore(unittest.TestCase):
    def test_public_demo_has_no_shared_anonymous_customer(self):
        store = SessionStore()
        with patch.object(settings, "public_demo", True), self.assertRaises(UnknownSession):
            store.get(None)
        self.assertEqual(store._bindings, {})

    def test_local_mode_keeps_the_single_customer(self):
        self.assertEqual(SessionStore().get(None).user_id, DEMO_USER)

    def test_bindings_are_bounded(self):
        store = SessionStore()
        with patch.object(SessionStore, "MAX_BINDINGS", 5):
            sessions = [store.get(None).session_id for _ in range(9)]
        self.assertLessEqual(len(store._bindings), 5)
        self.assertIn(sessions[-1], store._bindings)


class TestHttpPublicDemo(HttpBase):
    def test_anonymous_requests_get_404_on_public_demo(self):
        with patch.object(settings, "public_demo", True):
            self.assertEqual(self.get("/api/state").status_code, 404)
            self.assertEqual(self.post("/api/reset", {}).status_code, 404)
            self.assertEqual(self.post("/api/reset", {"visitor": "abababababababab"}).status_code, 200)

    def test_reset_is_rate_limited_on_public_demo(self):
        security._hits.clear()
        self.addCleanup(security._hits.clear)
        with patch.object(settings, "public_demo", True):
            codes = [self.post("/api/reset", {"visitor": "abababababababab"}).status_code for _ in range(security.COSTLY["/api/reset"] + 1)]
        self.assertEqual(codes[-1], 429)
        self.assertEqual(codes[0], 200)


class TestWorldResetIsAudited(HttpBase):
    def freeze_and_events(self):
        self.post("/api/freeze", {"session_id": self.sid})
        return len(runtime.audit.for_session(self.sid))

    def test_simulator_reset_works_but_leaves_an_audit_record_of_what_it_cleared(self):
        self.freeze_and_events()
        self.assertEqual(self.post("/api/reset", {"session_id": self.sid, "world": True}).status_code, 200)
        event = [e for e in runtime.audit.for_session(self.sid) if e["event"] == "demo.world_reset"][-1]
        self.assertEqual((event["data"]["was_frozen"], event["data"]["guardian"]), (True, "not_paired"))

    def test_outside_the_simulator_reset_cannot_clear_a_freeze_or_a_guardian(self):
        self.freeze_and_events()
        with patch.object(runtime.execution_gateway, "simulator", None):
            r = self.post("/api/reset", {"session_id": self.sid, "world": True})
        self.assertEqual((r.status_code, r.json()["code"]), (403, "PROTECTED_WORLD"), r.text)
        user = runtime.sessions.get(self.sid).user_id
        self.assertTrue(runtime.wallet.user(user)["frozen"]["on"])


class TestRateLimitIdentity(unittest.TestCase):
    PEER = {"client": ("10.0.0.9", 1234)}

    def test_forwarded_header_is_ignored_by_default(self):
        with patch.object(settings, "trusted_proxy_hops", 0):
            self.assertEqual(security._client(self.PEER, {"x-forwarded-for": "1.2.3.4"}), "10.0.0.9")

    def test_with_one_trusted_proxy_the_rightmost_address_is_the_client(self):
        with patch.object(settings, "trusted_proxy_hops", 1):
            self.assertEqual(security._client(self.PEER, {"x-forwarded-for": "6.6.6.6, 1.2.3.4"}), "1.2.3.4")
            self.assertEqual(security._client(self.PEER, {"x-forwarded-for": "7.7.7.7, 1.2.3.4"}), "1.2.3.4")   # the left part is forgeable

    def test_a_short_or_missing_header_falls_back_to_the_peer(self):
        with patch.object(settings, "trusted_proxy_hops", 2):
            self.assertEqual(security._client(self.PEER, {"x-forwarded-for": "1.2.3.4"}), "10.0.0.9")
            self.assertEqual(security._client(self.PEER, {}), "10.0.0.9")

    def test_routes_that_do_work_for_a_stranger_are_limited(self):
        for path in ("/api/lab", "/api/demo/model-mistake", "/api/reset"):
            self.assertIn(path, security.COSTLY)


class TestGuardianFeedLimit(HttpBase):
    def test_guardian_pages_behind_one_address_do_not_share_one_bucket(self):
        security._hits.clear()
        self.addCleanup(security._hits.clear)
        codes = {t: [] for t in ("anna", "ben")}
        with patch.dict(security.ALWAYS, {"/api/guardian/feed": 50}), patch.object(security, "FEED_PER_DEVICE", 2):
            for token in ("anna", "ben"):
                for _ in range(3):
                    codes[token].append(self.post("/api/guardian/feed", {"token": token}).status_code)
        self.assertEqual(codes["anna"], [403, 403, 429])       # unknown device: refused, and limited per token
        self.assertEqual(codes["ben"], [403, 403, 429])


class TestMalformedGuardianInput(World):
    def test_decide_rejects_bad_payloads_cleanly(self):
        self.pair_guardian()
        desk = self.gateway.guardian
        for bad in ("[]", "null", "3", '"x"', '{"draft_id":[]}', '{"draft_id":{"a":1}}', None, 7):
            with self.assertRaises(GuardianError) as e:
                desk.decide(USER, bad, "sig")
            self.assertIn(e.exception.code, ("BAD_REQUEST", "GUARDIAN_NOT_PENDING"), bad)

    def test_bad_expiry_is_refused_not_a_crash(self):
        self.pair_guardian()
        self.wallet.add_payee(USER, "Tom Reed", "tom.reed@family.example", "friend")
        self.turn("Pay Tom Reed 400 dollars")
        d = self.s.current_draft
        desk = self.gateway.guardian
        for expiry in ("x", None, [], True):
            with self.assertRaises(GuardianError) as e:
                desk.decide(USER, json.dumps({"draft_id": d.draft_id, "expires_at": expiry}), "sig")
            self.assertEqual(e.exception.code, "BAD_REQUEST")

    def test_lift_rejects_bad_payloads_cleanly(self):
        self.pair_guardian()
        desk = self.gateway.guardian
        for bad in ("[]", "null", '{"domain":"STAR-GUARDIAN-V1-LIFT","expires_at":"x"}', '{"domain":"STAR-GUARDIAN-V1-LIFT","expires_at":null}'):
            with self.assertRaises(GuardianError):
                desk.sign_lift(USER, bad, "sig")

    def test_verify_signed_with_a_non_object_payload_is_false(self):
        self.pair_guardian()
        self.assertFalse(self.gateway.guardian.verify_signed(USER, "d", "x", {"payload": "[]", "signature": "s"}, "approve"))


class TestDefenceInDepth(World):
    """Each of these has an outer check too, so disabling it alone used to go unnoticed."""

    def test_the_ledger_itself_refuses_while_frozen(self):
        inv = self.wallet.invoice(USER, catalogue.INVOICE_SEEDS[0]["invoice_id"])
        self.wallet.set_frozen(USER, True, "test")
        with self.assertRaises(ValueError) as e:
            self.wallet.begin_payment(bill_record(11, inv), reserve=True)
        self.assertIn("R14_WALLET_FROZEN", str(e.exception))

    def test_the_gateway_itself_refuses_an_expired_signature(self):
        from app.wallet.gateway import GRACE_SECONDS, GatewayError
        self.turn("Send 50 to my daughter")
        d = self.s.current_draft
        ch = self.kernel.issue_challenge(self.s, d.draft_id, d.digest)
        auth = self.signature(ch)
        real_time = time.time
        with patch("app.wallet.gateway.time.time", lambda: real_time() + settings.challenge_ttl + GRACE_SECONDS + 5), \
                self.assertRaises(GatewayError) as e:
            self.gateway._check(USER, self.s.session_id, d.card_fields(), d.digest, {**auth, "method": "device_key"})
        self.assertEqual(e.exception.code, "GW_EXPIRED")


class TestNotOwnIdeaStop(HttpBase):
    def test_saying_it_was_not_her_idea_records_a_stop_and_tells_the_family(self):
        user = runtime.sessions.get(self.sid).user_id
        tom = runtime.wallet.add_payee(user, "Tom Reed", "tom.reed@family.example", "friend")
        self.assertTrue(tom["payee_id"])
        card = self.card("Pay Tom Reed 40 dollars")
        r = self.post("/api/cancel", {"session_id": self.sid, "draft_id": card["draft_id"], "reason": "new_payee_not_own_idea"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("very likely a scam", r.json()["reply"])
        self.assertEqual(runtime.wallet.stops(user)[0]["codes"], ["NEW_PAYEE_NOT_OWN_IDEA"])
        self.assertIn("wasn't her own idea", runtime.wallet.user(user)["alerts"][0]["text"])


class TestSigningSecret(unittest.TestCase):
    def start(self, **env):
        clean = {k: v for k, v in os.environ.items() if k not in ("PUBLIC_DEMO", "PAYPAL_MODE")}
        return subprocess.run([sys.executable, "-c", "import app.runtime"], cwd=ROOT, capture_output=True, text=True,
                              env={**clean, "LLM_PROVIDER": "offline", "SIGNING_SECRET": "", **env}, timeout=60)

    def test_problem_is_named_for_a_shared_or_sandbox_server(self):
        from app.config import secret_problem
        self.assertIn("SIGNING_SECRET", secret_problem(b"dev-only-change-me", public_demo=True, mode="simulator"))
        self.assertIn("SIGNING_SECRET", secret_problem(b"dev-only-change-me", public_demo=False, mode="sandbox"))
        self.assertEqual(secret_problem(b"dev-only-change-me", public_demo=False, mode="simulator"), "")
        self.assertEqual(secret_problem(b"a-real-one", public_demo=True, mode="sandbox"), "")

    def test_a_public_server_without_a_secret_refuses_to_start(self):
        r = self.start(PUBLIC_DEMO="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SIGNING_SECRET", r.stderr)


if __name__ == "__main__":
    unittest.main()
