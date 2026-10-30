"""The family guardian over HTTP: pairing, exact-card binding, and who can decide. Without a live PayPal or model."""
import base64
import unittest

from starlette.testclient import TestClient

from app import runtime, security
from app.kernel import p256
from app.kernel.signing import build_settings_challenge
from app.server import app


class GuardianHttpTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost")
        self.addCleanup(self.client.close)
        security._hits.clear()
        self.s = runtime.sessions.new_for_visitor("abcdef0123456789")
        self.key, self.jwk = p256.generate_keypair()
        runtime.kernel.register_key(self.s, self.jwk)
        self.token = self._pair()
        p = runtime.wallet.add_payee(self.s.user_id, "Tom Reed", "tom.reed@mail.example", "friend")
        self.payee = p["payee_id"]
        runtime.agent.run_turn(self.s, "Send 400 to Tom Reed")
        self.d = self.s.current_draft

    def _sign(self, key, payload: str) -> str:
        return base64.b64encode(p256.sign(key, payload.encode())).decode()

    def _pair(self) -> str:
        payload = self.client.post("/api/guardian/invite/challenge", json={"session_id": self.s.session_id}).json()["payload"]
        r = self.client.post("/api/guardian/invite", json={"session_id": self.s.session_id,
                                                           "assertion": {"payload": payload, "signature": self._sign(self.key, payload)}})
        self.assertEqual(r.status_code, 200, r.text)
        self.gkey, gjwk = p256.generate_keypair()
        r = self.client.post("/api/guardian/pair", json={"invite_token": r.json()["invite_token"], "public_key": gjwk})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["token"]

    def _decide(self, decision="approve", key=None, draft=None):
        draft = draft or self.d
        payload = self.client.post("/api/guardian/challenge", json={"token": self.token, "draft_id": draft.draft_id, "decision": decision}).json()["payload"]
        return self.client.post("/api/guardian/decide", json={"token": self.token, "payload": payload, "signature": self._sign(key or self.gkey, payload)})

    def test_invite_needs_her_signature(self):
        r = self.client.post("/api/guardian/invite", json={"session_id": self.s.session_id, "assertion": {"payload": "x", "signature": "y"}})
        self.assertEqual(r.json()["code"], "PAYLOAD_MISMATCH")

    def test_feed_shows_the_exact_card_and_needs_the_token(self):
        feed = self.client.post("/api/guardian/feed", json={"token": self.token}).json()
        row = feed["pending"][0]
        self.assertEqual((row["draft_id"], row["digest"], row["amount"]), (self.d.draft_id, self.d.digest, "$400.00"))
        self.assertIn("doesn't send real texts or emails", feed["note"])
        self.assertEqual(self.client.post("/api/guardian/feed", json={"token": "wrong"}).status_code, 403)

    def test_approve_never_moves_money(self):
        before = runtime.wallet.allowance_left(self.s.user_id)
        r = self._decide()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(runtime.wallet.allowance_left(self.s.user_id), before)
        self.assertFalse(runtime.wallet.payments_for(self.s.user_id))
        card = self.client.get("/api/card/" + self.s.session_id).json()["card"]
        self.assertFalse(card["guardian_required"])
        self.assertEqual(card["decision"]["checks"]["authorization"]["status"], "pending")

    def test_deny_kills_it(self):
        self.assertEqual(self._decide("deny").json()["decision"], "deny")
        self.assertIsNone(self.s.current_draft)
        rows = self.client.get("/api/activity", params={"session_id": self.s.session_id}).json()["rows"]
        self.assertEqual(rows[0]["status"], "DENIED")

    def test_another_key_cannot_decide(self):
        other, _ = p256.generate_keypair()
        r = self._decide(key=other)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "GUARDIAN_SIGNATURE"))
        self.assertIsNone(runtime.kernel.approvals.valid(self.s.user_id, self.s.session_id, self.d.draft_id, self.d.digest))

    def test_cancelled_card_refused(self):
        runtime.kernel.cancel(self.s, self.d.draft_id)
        r = self.client.post("/api/guardian/challenge", json={"token": self.token, "draft_id": self.d.draft_id, "decision": "approve"})
        self.assertEqual(r.json()["code"], "GUARDIAN_NOT_PENDING")

    def test_guardian_can_freeze_and_lifting_needs_both(self):
        self.client.post("/api/guardian/freeze", json={"token": self.token})
        self.assertTrue(runtime.wallet.user(self.s.user_id)["frozen"]["on"])
        payload = self.client.post("/api/freeze/lift/challenge", json={"session_id": self.s.session_id}).json()["payload"]
        r = self.client.post("/api/freeze/lift", json={"session_id": self.s.session_id, "assertion": {"payload": payload, "signature": self._sign(self.key, payload)}})
        self.assertTrue(r.json()["frozen"]["on"])                  # her half alone isn't enough
        payload = self.client.post("/api/guardian/lift/challenge", json={"token": self.token}).json()["payload"]
        r = self.client.post("/api/guardian/lift", json={"token": self.token, "payload": payload, "signature": self._sign(self.gkey, payload)})
        self.assertFalse(r.json()["frozen"]["on"])

    def test_rate_limited(self):
        for _ in range(60):
            self.client.post("/api/guardian/challenge", json={"token": "x", "draft_id": "y", "decision": "approve"})
        self.assertEqual(self.client.post("/api/guardian/challenge", json={"token": "x", "draft_id": "y", "decision": "approve"}).status_code, 429)
        _ = build_settings_challenge
