"""Regressions from the code review: races around a payment in flight, swapping the guardian, and the small-then-large scam."""
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

os.environ.setdefault("RULE_FIXED_HOUR", "14")     # before anything imports app.config: results can't depend on the clock

from app import runtime  # noqa: E402
from app.kernel import p256  # noqa: E402
from app.kernel.executor import KernelError  # noqa: E402
from app.models import Phase  # noqa: E402
from app.wallet.guardian import GuardianError  # noqa: E402
from tests.paypal_helpers import USER, World  # noqa: E402
from tests.test_paypal_http import Base as HttpBase  # noqa: E402


class TestCancelWhilePaying(HttpBase):
    def slow_submit(self, started):
        rec = runtime.execution_gateway.reconciler
        real = rec.submit

        def submit(r):
            started.set()
            time.sleep(0.5)
            return real(r)
        return patch.object(rec, "submit", submit)

    def test_cancel_during_the_paypal_call_never_says_nothing_was_sent(self):
        card = self.card("Send 50 to my daughter")
        body = self.confirm_body(card)
        started = threading.Event()
        with self.slow_submit(started), ThreadPoolExecutor(1) as pool:
            confirm = pool.submit(self.post, "/api/confirm", body)
            self.assertTrue(started.wait(5))
            cancelled = self.post("/api/cancel", {"session_id": self.sid, "draft_id": card["draft_id"]})
            confirmed = confirm.result()
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual((cancelled.status_code, cancelled.json()["code"]), (409, "ALREADY_SENT"), cancelled.text)
        self.assertNotIn("No money", cancelled.text)
        self.assertEqual(confirmed.json()["payment"]["state"], "SENT")

    def test_cancel_before_any_signature_still_cancels(self):
        card = self.card("Send 50 to my daughter")
        r = self.post("/api/cancel", {"session_id": self.sid, "draft_id": card["draft_id"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("No money was sent", r.json()["reply"])


class TestResetWhilePaying(HttpBase):
    def test_a_world_reset_waits_for_a_confirm_already_running(self):
        from app import server
        user = runtime.sessions.get(self.sid).user_id
        lock = server._user_lock(user)
        with ThreadPoolExecutor(1) as pool:
            lock.acquire()                                       # stands in for a confirm in flight
            try:
                pending = pool.submit(self.post, "/api/reset", {"session_id": self.sid, "world": True})
                time.sleep(0.4)
                self.assertFalse(pending.done(), "reset wiped the customer's records while her confirm held the lock")
            finally:
                lock.release()
            self.assertEqual(pending.result(10).status_code, 200)


class TestKernelPhaseWhilePaying(World):
    def test_the_kernel_itself_refuses_cancel_and_deny_while_the_gateway_runs(self):
        self.pair_guardian()
        self.turn("Send 50 to my daughter")
        d = self.s.current_draft
        ch = self.kernel.issue_challenge(self.s, d.draft_id, d.digest)
        token = self.kernel.authorize(self.s, d.draft_id, d.digest, self.signature(ch))
        seen, real = {}, self.gateway.execute

        def execute(**kw):
            seen["phase"] = self.s.phase
            for fn in (lambda: self.kernel.cancel(self.s, d.draft_id), lambda: self.kernel.deny_guardian(self.s, d.draft_id, d.digest)):
                with self.assertRaises(KernelError) as e:
                    fn()
                self.assertEqual(e.exception.code, "ALREADY_SENT")
            return real(**kw)
        with patch.object(self.gateway, "execute", execute):
            self.kernel.commit(self.s, d.draft_id, token)
        self.assertEqual(seen["phase"], Phase.EXECUTING)
        self.assertEqual(self.s.phase, Phase.EXECUTED)

    def test_an_unexpected_gateway_crash_does_not_wedge_the_session(self):
        self.turn("Send 50 to my daughter")
        d = self.s.current_draft
        ch = self.kernel.issue_challenge(self.s, d.draft_id, d.digest)
        token = self.kernel.authorize(self.s, d.draft_id, d.digest, self.signature(ch))
        with patch.object(self.gateway, "execute", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
            self.kernel.commit(self.s, d.draft_id, token)
        self.assertEqual(self.s.phase, Phase.CANCELLED)        # no record was written, so nothing moved
        self.s.settle()
        self.assertEqual(self.s.phase, Phase.IDLE)


class TestGuardianCannotBeSwapped(World):
    def test_a_second_invite_is_refused_while_paired_and_everyone_is_told(self):
        self.pair_guardian()
        desk = self.gateway.guardian
        with self.assertRaises(GuardianError) as e:
            desk.invite(USER)
        self.assertEqual(e.exception.code, "GUARDIAN_ALREADY_PAIRED")
        self.assertEqual(desk.user_for_token(self.guardian_token), USER)      # Anna's device still works
        self.assertIn("replace", self.wallet.user(USER)["alerts"][0]["text"])
        self.assertTrue(any(x["event"] == "guardian.replace_refused" for x in self.audit.for_session("")))

    def test_an_old_invite_cannot_replace_a_paired_guardian(self):
        desk = self.gateway.guardian
        old = desk.invite(USER)
        self.pair_guardian()
        self.wallet.user(USER)["guardian"]["invite"] = {"token_sha256": __import__("hashlib").sha256(old.encode()).hexdigest(),
                                                        "expires_at": time.time() + 60}
        with self.assertRaises(GuardianError) as e:
            desk.pair(old, p256.generate_keypair()[1])
        self.assertEqual(e.exception.code, "GUARDIAN_ALREADY_PAIRED")
        self.assertEqual(desk.user_for_token(self.guardian_token), USER)

    def test_a_pending_removal_does_not_open_the_door_early(self):
        self.pair_guardian()
        desk = self.gateway.guardian
        desk.request_removal(USER)
        with self.assertRaises(GuardianError):
            desk.invite(USER)

    def test_replacing_works_after_the_removal_delay(self):
        self.pair_guardian()
        desk = self.gateway.guardian
        desk.request_removal(USER)
        self.wallet.user(USER)["guardian"]["removal"]["effective_at"] = time.time() - 1
        old_token = self.guardian_token
        self.pair_guardian()                                                   # invite then pair, as the app does
        with self.assertRaises(GuardianError):
            desk.user_for_token(old_token)
        self.assertEqual(desk.user_for_token(self.guardian_token), USER)


class TestSmallThenLargeScam(World):
    def setUp(self):
        super().setUp()
        self.wallet.add_payee(USER, "Tom Reed", "tom.reed@family.example", "friend")

    def small_payment(self):
        self.turn("Pay Tom Reed 5 dollars")
        receipt = self.sign()
        self.assertEqual(self.rec(receipt)["state"], "SENT")

    def test_a_small_test_payment_does_not_remove_the_family_check(self):
        self.pair_guardian()
        self.small_payment()
        self.turn("Pay Tom Reed 450 dollars")
        d = self.s.current_draft
        self.assertEqual(d.decision["checks"]["guardian_approval"]["status"], "pending")
        self.assertEqual(self.s.phase, Phase.COOLING)
        self.assertIn("R15_NEW_PAYEE_RAMP", d.risk.rule_hits)
        self.assertEqual(d.risk.verify_kind, "new_payee")
        self.assertEqual(self.gateway.guardian.pending(USER)[0]["draft_id"], d.draft_id)

    def test_many_small_payments_add_up(self):
        self.pair_guardian()
        self.small_payment()
        self.turn("Pay Tom Reed 299 dollars")                                  # $5 + $299 is over the $300 threshold
        self.assertEqual(self.s.current_draft.decision["checks"]["guardian_approval"]["status"], "pending")

    def test_a_payment_still_in_flight_counts_towards_the_total(self):
        self.pair_guardian()
        self.small_payment()
        rec = next(r for r in self.wallet.payments_for(USER) if r["counterparty_name"] == "Tom Reed")
        self.wallet.user(USER)["transactions"] = [t for t in self.wallet.user(USER)["transactions"] if t.get("payment_id") != rec["payment_id"]]
        self.turn("Pay Tom Reed 299 dollars")                                  # no verified transaction: it still counts
        self.assertEqual(self.s.current_draft.decision["checks"]["guardian_approval"]["status"], "pending")

    def test_small_amounts_stay_easy(self):
        self.pair_guardian()
        self.small_payment()
        self.turn("Pay Tom Reed 6 dollars")
        self.assertEqual(self.s.current_draft.decision["checks"]["guardian_approval"]["status"], "not_applicable")

    def test_her_family_on_the_seeded_list_is_unchanged(self):
        self.pair_guardian()
        self.turn("Send 200 to my daughter")
        self.assertEqual(self.s.current_draft.decision["checks"]["guardian_approval"]["status"], "not_applicable")

    def test_the_gateway_recheck_also_refuses_it_without_the_guardian(self):
        self.pair_guardian()
        self.small_payment()
        self.turn("Pay Tom Reed 450 dollars")
        d = self.s.current_draft
        d.cooling_until = 0
        self.kernel._refresh_cooling(self.s, d)
        with self.assertRaises(KernelError) as e:
            self.kernel.issue_challenge(self.s, d.draft_id, d.digest, verify_answer=True)
        self.assertEqual(e.exception.code, "GUARDIAN_APPROVAL_REQUIRED")

    def test_approved_then_the_exact_card_goes_through_once(self):
        self.pair_guardian()
        self.small_payment()
        self.turn("Pay Tom Reed 450 dollars")
        d = self.s.current_draft
        self.guardian_decides(d)
        self.assertEqual(self.rec(self.sign(d))["state"], "SENT")


if __name__ == "__main__":
    unittest.main()
