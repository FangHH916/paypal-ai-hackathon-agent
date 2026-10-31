"""The family guardian: pairing, an approval bound to one exact card, and what an approval can never do (I9)."""
import base64
import time

from app.kernel import p256
from app.kernel.executor import KernelError
from app.models import Phase
from app.wallet.guardian import GuardianError
from tests.paypal_helpers import USER, World


class Base(World):
    def setUp(self):
        super().setUp()
        self.stranger = self.wallet.add_payee(USER, "Tom Reed", "tom.reed@family.example", "friend")["payee_id"]

    def risky(self):
        self.turn("Pay Tom Reed 400 dollars")
        return self.s.current_draft


class TestPairing(Base):
    def test_pair_then_token_works(self):
        self.pair_guardian()
        self.assertEqual(self.gateway.guardian.user_for_token(self.guardian_token), USER)
        self.assertEqual(self.gateway.guardian.status(USER)["status"], "paired")

    def test_invite_is_one_time(self):
        desk = self.gateway.guardian
        token = desk.invite(USER)
        desk.pair(token, p256.generate_keypair()[1])
        with self.assertRaises(GuardianError) as e:
            desk.pair(token, p256.generate_keypair()[1])
        self.assertEqual(e.exception.code, "INVITE_INVALID")

    def test_invite_expires(self):
        desk = self.gateway.guardian
        token = desk.invite(USER)
        self.wallet.user(USER)["guardian"]["invite"]["expires_at"] = time.time() - 1
        with self.assertRaises(GuardianError) as e:
            desk.pair(token, p256.generate_keypair()[1])
        self.assertEqual(e.exception.code, "INVITE_EXPIRED")

    def test_wrong_token_and_bad_key(self):
        desk = self.gateway.guardian
        with self.assertRaises(GuardianError):
            desk.user_for_token("nope")
        with self.assertRaises(GuardianError):
            desk.pair(desk.invite(USER), {"kty": "EC", "crv": "P-256", "x": "AA", "y": "AA"})

    def test_token_and_private_key_not_stored_in_the_clear(self):
        self.pair_guardian()
        g = self.wallet.user(USER)["guardian"]
        self.assertNotEqual(g["token_sha256"], self.guardian_token)
        self.assertNotIn("d", g["key_jwk"])

    def test_removal_is_delayed(self):
        self.pair_guardian()
        st = self.gateway.guardian.request_removal(USER)
        self.assertEqual(st["status"], "paired")                    # still there during the delay
        self.wallet.user(USER)["guardian"]["removal"]["effective_at"] = time.time() - 1
        self.assertEqual(self.gateway.guardian.status(USER)["status"], "not_paired")


class TestApproval(Base):
    def test_a_risky_payment_waits_and_the_exact_card_is_on_her_page(self):
        self.pair_guardian()
        d = self.risky()
        row = self.gateway.guardian.feed(USER)["pending"][0]
        self.assertEqual((row["draft_id"], row["digest"], row["amount"]), (d.draft_id, d.digest, "$400.00"))
        d.cooling_until = 0
        self.kernel._refresh_cooling(self.s, d)
        with self.assertRaises(KernelError) as e:
            self.kernel.issue_challenge(self.s, d.draft_id, d.digest, verify_answer=True)
        self.assertEqual(e.exception.code, "GUARDIAN_APPROVAL_REQUIRED")

    def test_approve_then_she_can_sign_and_the_gateway_rechecks(self):
        self.pair_guardian()
        d = self.risky()
        self.guardian_decides(d)
        rec = self.rec(self.sign(d))
        self.assertEqual(rec["state"], "SENT")
        self.assertTrue(rec["decision"]["guardian_approval"]["approval_id"])

    def test_no_guardian_means_it_waits_fail_closed(self):
        d = self.risky()
        d.cooling_until = 0
        self.kernel._refresh_cooling(self.s, d)
        with self.assertRaises(KernelError):
            self.kernel.issue_challenge(self.s, d.draft_id, d.digest, verify_answer=True)
        self.assertEqual(self.writes(), [])

    def test_denial_kills_it_and_nothing_is_sent(self):
        self.pair_guardian()
        d = self.risky()
        self.guardian_decides(d, "deny")
        self.assertEqual(self.s.phase, Phase.CANCELLED)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.wallet.stops(USER)[0]["stopped_by"], "Your family said no")
        with self.assertRaises(KernelError):
            self.kernel.approve_guardian(self.s, d.draft_id, d.digest)

    def test_a_wrong_key_is_refused(self):
        self.pair_guardian()
        d = self.risky()
        other, _ = p256.generate_keypair()
        with self.assertRaises(GuardianError) as e:
            self.guardian_decides(d, key=other)
        self.assertEqual(e.exception.code, "GUARDIAN_SIGNATURE")

    def test_a_signature_cannot_be_replayed(self):
        key = self.pair_guardian()
        d = self.risky()
        desk = self.gateway.guardian
        payload = desk.challenge(USER, d.draft_id, "approve")
        sig = base64.b64encode(p256.sign(key, payload.encode())).decode()
        desk.decide(USER, payload, sig)
        with self.assertRaises(GuardianError):
            desk.decide(USER, payload, sig)

    def test_an_approve_signature_cannot_be_read_as_a_deny(self):
        key = self.pair_guardian()
        d = self.risky()
        desk = self.gateway.guardian
        payload = desk.challenge(USER, d.draft_id, "approve")
        signed = {"payload": payload, "signature": base64.b64encode(p256.sign(key, payload.encode())).decode()}
        self.assertTrue(desk.verify_signed(USER, d.draft_id, d.digest, signed, "approve"))
        self.assertFalse(desk.verify_signed(USER, d.draft_id, d.digest, signed, "deny"))

    def test_approval_is_bound_to_the_digest(self):
        self.pair_guardian()
        d = self.risky()
        self.guardian_decides(d)
        d.amount_cents = 90000                                       # the card changes after she approved it
        with self.assertRaises(KernelError):
            self.kernel.issue_challenge(self.s, d.draft_id, d.digest, verify_answer=True)
        self.assertEqual(self.writes(), [])

    def test_approval_for_another_digest_is_refused(self):
        self.pair_guardian()
        d = self.risky()
        with self.assertRaises(KernelError) as e:
            self.kernel.approve_guardian(self.s, d.draft_id, "0" * 64)
        self.assertEqual(e.exception.code, "DIGEST_MISMATCH")


class TestWhatAnApprovalCanNeverDo(Base):
    def test_cannot_unlock_a_block(self):
        self.pair_guardian()
        self.turn("Get me a blood pressure monitor from MedDeals Direct")
        d = self.s.current_draft
        self.assertEqual(self.s.phase, Phase.BLOCKED)
        self.assertEqual(self.gateway.guardian.pending(USER), [])     # nothing even asks her
        with self.assertRaises(KernelError):
            self.kernel.approve_guardian(self.s, d.draft_id, d.digest)

    def test_cannot_unlock_a_frozen_draft(self):
        self.pair_guardian()
        self.s.messages.append({"role": "user", "content": "Send 50 to my daughter"})
        self.tools.call(self.s, "draft_transfer", {"payee_id": "p_daughter", "amount": 500})
        d = self.s.current_draft
        self.assertEqual(self.s.phase, Phase.FROZEN)
        self.assertEqual(self.gateway.guardian.pending(USER), [])
        with self.assertRaises(KernelError):
            self.kernel.approve_guardian(self.s, d.draft_id, d.digest)

    def test_guardian_has_no_way_to_start_a_payment(self):
        desk = self.gateway.guardian
        self.pair_guardian()
        self.assertFalse([n for n in dir(desk) if "draft" in n or "pay" in n.split("_") or "execute" in n])
        with self.assertRaises(GuardianError):
            desk.challenge(USER, "d_made_up", "approve")

    def test_approval_is_not_a_signature(self):
        self.pair_guardian()
        d = self.risky()
        self.guardian_decides(d)
        self.assertEqual(self.writes(), [])                           # she still has to sign


class TestFreeze(Base):
    def test_freeze_blocks_and_lifting_needs_both_when_paired(self):
        self.pair_guardian()
        self.kernel.freeze(self.s, "the customer")
        self.turn("Send 50 to my daughter")
        self.assertIn("R14_WALLET_FROZEN", self.s.current_draft.risk.rule_hits)
        self.s.settle()
        ch = self.kernel.lift_freeze_challenge(self.s)
        self.assertTrue(self.kernel.lift_freeze(self.s, self.signature(ch))["on"])
        self.assertFalse(self.kernel.finish_lift(USER, "", "guardian")["on"])

    def test_lifting_without_a_signature_refused(self):
        self.kernel.freeze(self.s)
        with self.assertRaises(KernelError):
            self.kernel.lift_freeze(self.s, None)
        self.assertTrue(self.wallet.user(USER)["frozen"]["on"])

    def test_alone_she_can_lift_when_no_guardian_is_paired(self):
        self.kernel.freeze(self.s)
        ch = self.kernel.lift_freeze_challenge(self.s)
        self.assertFalse(self.kernel.lift_freeze(self.s, self.signature(ch))["on"])

    def test_a_payment_already_running_cannot_slip_past_a_freeze(self):
        self.turn("Send 50 to my daughter")
        d = self.s.current_draft
        self.kernel.freeze(self.s)
        with self.assertRaises(KernelError) as e:
            self.kernel.issue_challenge(self.s, d.draft_id, d.digest)
        self.assertEqual(e.exception.code, "RECHECK_BLOCKED")
        self.assertEqual(self.writes(), [])
