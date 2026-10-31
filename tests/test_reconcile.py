"""Each row of the reconciliation matrix: an unknown outcome is resolved by asking, with the same key, and never pays twice."""
import json
import time
from unittest.mock import patch

from app.config import settings
from app.paypal.errors import PayPalError
from app.paypal.reconcile import batch_id_from_links
from app.wallet.ledger import Wallet
from tests.paypal_helpers import USER, World


class Base(World):
    def setUp(self):
        super().setUp()
        p = patch.object(settings, "velocity_max", 50)
        p.start()
        self.addCleanup(p.stop)

    def payout_posts(self):
        return self.writes("/v1/payments/payouts")


class TestPayoutUnknown(Base):
    def lost(self, fault="timeout_after_commit"):
        self.sim.arm(USER, fault, op="payout_create")
        self.turn("Send 50 to my daughter")
        return self.rec(self.sign())

    def test_a_lost_reply_is_unknown_not_failed_and_star_says_so(self):
        from app.agent.presenter import receipt_text
        self.sim.arm(USER, "timeout_after_commit", op="payout_create")
        self.turn("Send 50 to my daughter")
        d = self.s.current_draft
        r = self.sign()
        self.assertEqual((r["status"], r["state"]), ("UNKNOWN", "UNKNOWN"))
        text = receipt_text(d, r)
        self.assertIn("can't confirm", text)
        self.assertIn("don't pay again", text)
        self.assertEqual(self.wallet.allowance_left(USER), 145000)     # stays reserved while unknown

    def test_lost_reply_then_exactly_one_payout(self):
        rec = self.lost()
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual((rec["state"], rec["verified"]), ("SENT", True))
        self.assertEqual(len(self.sim.st["batches"]), 1)
        posts = self.payout_posts()
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]["body"], posts[1]["body"])          # identical body
        self.assertEqual(posts[0]["headers"]["paypal-request-id"], posts[1]["headers"]["paypal-request-id"])

    def test_timeout_before_commit_then_one_payout(self):
        rec = self.lost("timeout_before_commit")
        self.assertEqual(self.sim.st["batches"], {})
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual(rec["state"], "SENT")
        self.assertEqual(len(self.sim.st["batches"]), 1)

    def test_5xx_then_one_payout(self):
        rec = self.lost("http_500")
        self.assertEqual(rec["state"], "UNKNOWN")
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual(rec["state"], "SENT")
        self.assertEqual(len(self.sim.st["batches"]), 1)

    def test_still_failing_stays_unknown_and_keeps_the_key(self):
        rec = self.lost()
        self.sim.arm(USER, "timeout_before_commit", op="payout_create", count=2)
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual(rec["state"], "UNKNOWN")
        self.assertEqual(len({p["headers"]["paypal-request-id"] for p in self.payout_posts()}), 1)

    def test_duplicate_rejection_with_a_link_finds_the_original(self):
        rec = self.lost()
        bid = next(iter(self.sim.st["batches"]))
        err = PayPalError(400, "USER_BUSINESS_ERROR", "dup", links=[{"href": f"https://x/v1/payments/payouts/{bid}", "rel": "self"}])
        self.assertEqual(batch_id_from_links(err), bid)
        with patch.object(self.gateway.client, "create_payout", side_effect=err):
            self.rec_mod.refresh(rec, force=True)
        self.assertEqual((rec["state"], rec["paypal"]["batch_id"]), ("SENT", bid))

    def test_duplicate_rejection_without_a_link_stays_unknown(self):
        rec = self.lost()
        with patch.object(self.gateway.client, "create_payout", side_effect=PayPalError(400, "X", "dup")):
            self.rec_mod.refresh(rec, force=True)
        self.assertEqual(rec["state"], "UNKNOWN")

    def test_new_draft_for_the_same_payee_is_blocked_while_unknown(self):
        self.lost()
        r = self.turn("Send 50 to my daughter")
        d = self.s.current_draft
        self.assertIn("R60_UNKNOWN_OUTCOME", d.risk.rule_hits)
        self.assertEqual(r.ui["type"], "blocked")

    def test_any_payee_with_the_same_amount_is_blocked_too(self):
        self.lost()
        self.turn("Send 50 to my son")
        self.assertIn("R60_UNKNOWN_OUTCOME", self.s.current_draft.risk.rule_hits)

    def test_recent_duplicate_asks(self):
        self.turn("Send 50 to my daughter")
        self.sign()
        self.turn("Send 50 to my daughter")
        d = self.s.current_draft
        self.assertIn("R61_RECENT_DUPLICATE", d.risk.rule_hits)
        self.assertIsNotNone(d.risk.verify_question)

    def test_stuck_unknown_alerts_the_family(self):
        rec = self.lost()
        rec["updated_at"] = time.time() - settings.unknown_alert_minutes * 60 - 1
        self.sim.arm(USER, "timeout_before_commit", op="payout_create")
        self.rec_mod.refresh(rec, force=True)
        self.assertTrue(any(a["kind"] == "unknown" for a in self.wallet.user(USER)["alerts"]))


class TestDoubleClick(Base):
    def test_gateway_returns_the_same_record(self):
        self.turn("Send 50 to my daughter")
        d = self.s.current_draft
        ch = self.kernel.issue_challenge(self.s, d.draft_id, d.digest)
        tok = self.kernel.authorize(self.s, d.draft_id, d.digest, self.signature(ch))
        first = self.kernel.commit(self.s, d.draft_id, tok)
        with self.assertRaises(Exception):                            # the same signature again: refused by the replay list
            self.gateway.execute(user_id=USER, session_id=self.s.session_id, card=d.card_fields(), digest=d.digest, auth=self.s.signatures[d.draft_id])
        self.assertEqual(len(self.payout_posts()), 1)
        self.assertEqual(self.wallet.payment(first["payment_id"])["state"], "SENT")


class TestOrderUnknown(Base):
    def test_create_lost_then_same_request_id_finds_the_order(self):
        self.sim.arm(USER, "timeout_after_commit", op="order_create")
        self.turn("Get me a blood pressure monitor")
        rec = self.rec(self.sign())
        self.assertEqual(rec["state"], "UNKNOWN")
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual(rec["state"], "AWAITING_APPROVAL")
        self.assertEqual(len(self.sim.st["orders"]), 1)

    def test_create_unknown_after_the_key_window_is_abandoned(self):
        self.sim.arm(USER, "timeout_before_commit", op="order_create")
        self.turn("Get me a blood pressure monitor")
        rec = self.rec(self.sign())
        rec["created_at"] -= 7 * 3600
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual(rec["state"], "ABANDONED")
        self.assertEqual(self.wallet.allowance_left(USER), 150000)

    def approved(self):
        self.turn("Get me a blood pressure monitor")
        rec = self.rec(self.sign())
        self.sim.buyer_decide_order(rec["paypal"]["order_id"], "approve")
        return rec

    def test_capture_lost_is_found_by_get(self):
        rec = self.approved()
        self.sim.arm(USER, "timeout_after_commit", op="order_capture")
        self.rec_mod.order_return(rec)
        self.assertEqual(rec["state"], "UNKNOWN")
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual((rec["state"], rec["verified"]), ("PAID", True))
        self.assertEqual(len(self.sim.st["captures"]), 1)
        self.assertEqual(len(self.writes("/capture")), 1)             # found by GET: no second capture call at all

    def test_capture_that_never_happened_retries_with_the_same_id(self):
        rec = self.approved()
        self.sim.arm(USER, "timeout_before_commit", op="order_capture")
        self.rec_mod.order_return(rec)
        self.assertEqual(rec["state"], "UNKNOWN")
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual(rec["state"], "PAID")
        keys = {w["headers"]["paypal-request-id"] for w in self.writes("/capture")}
        self.assertEqual(keys, {"cap-" + rec["digest"][:40]})

    def test_second_return_during_unknown_does_not_double_capture(self):
        rec = self.approved()
        self.sim.arm(USER, "timeout_after_commit", op="order_capture")
        self.rec_mod.order_return(rec)
        self.rec_mod.order_return(rec)
        self.assertEqual(len(self.sim.st["captures"]), 1)


class TestRefundUnknown(Base):
    def test_lost_refund_same_key_one_refund(self):
        self.buy()
        self.sim.arm(USER, "timeout_after_commit", op="capture_refund")
        self.turn("I want my money back")
        rec = self.rec(self.sign())
        self.assertEqual(rec["state"], "UNKNOWN")
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual((rec["state"], rec["verified"]), ("REFUNDED", True))
        self.assertEqual(len(self.sim.st["refunds"]), 1)
        self.assertEqual(self.wallet.allowance_left(USER), 150000)    # given back once, not twice

    def test_lost_cancel_resolves_by_get(self):
        self.wallet.add_payee(USER, "Tom Reed", "tom.reed@mail.example", "friend")
        self.turn("Send 20 to Tom Reed")
        parent = self.rec(self.sign())
        self.sim.arm(USER, "timeout_after_commit", op="payout_item_cancel")
        self.turn("Stop that payment")
        rec = self.rec(self.sign())
        self.assertEqual(rec["state"], "UNKNOWN")
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual((rec["state"], parent["state"]), ("REFUNDED", "RETURNED"))


class TestInvoiceUnknown(Base):
    def test_lost_create_is_found_by_number_no_second_invoice(self):
        self.sim.arm(USER, "timeout_after_commit", op="invoice_create")
        self.turn("Ask my son to pay me back 40 dollars")
        rec = self.rec(self.sign())
        self.assertEqual(rec["state"], "UNKNOWN")
        before = len(self.sim.st["invoices"])
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual(rec["state"], "SENT_TO_PAYER")
        self.assertEqual(len(self.sim.st["invoices"]), before)
        self.assertEqual(len(self.writes("/v2/invoicing/invoices")), 1)

    def test_create_that_never_happened_creates_it(self):
        self.sim.arm(USER, "timeout_before_commit", op="invoice_create")
        self.turn("Ask my son to pay me back 40 dollars")
        rec = self.rec(self.sign())
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual(rec["state"], "SENT_TO_PAYER")

    def test_lost_send_sends_once(self):
        self.sim.arm(USER, "timeout_before_commit", op="invoice_send")
        self.turn("Ask my son to pay me back 40 dollars")
        rec = self.rec(self.sign())
        self.assertEqual(rec["state"], "UNKNOWN")
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual(rec["state"], "SENT_TO_PAYER")


class TestRestart(Base):
    def test_a_record_left_mid_call_becomes_unknown_then_resolves(self):
        from app.paypal import calls
        self.turn("Send 50 to my daughter")
        d = self.s.current_draft
        ch = self.kernel.issue_challenge(self.s, d.draft_id, d.digest)
        tok = self.kernel.authorize(self.s, d.draft_id, d.digest, self.signature(ch))
        with patch.object(self.rec_mod, "submit", side_effect=KeyboardInterrupt):     # the process dies during the HTTP call
            with self.assertRaises(KeyboardInterrupt):
                self.kernel.commit(self.s, d.draft_id, tok)
        rec = self.wallet.payments_for(USER)[0]
        self.assertEqual(rec["state"], "SUBMITTING")                  # the write-ahead record is what survives
        self.assertEqual(self.rec_mod.recover_at_startup(), 1)
        self.assertEqual(rec["state"], "UNKNOWN")
        with calls.scope(USER):
            self.rec_mod.refresh(rec, force=True)
        self.assertEqual(rec["state"], "SENT")
        self.assertEqual(len(self.sim.st["batches"]), 1)

    def test_records_and_dedup_survive_a_reopen(self):
        import os
        path = os.path.join(self.tmp.name, "w.sqlite3")
        w = Wallet(path)
        self.assertTrue(w.note_webhook_event("WH-1", "x"))
        self.assertFalse(Wallet(path).note_webhook_event("WH-1", "x"))


class TestWriteAhead(Base):
    def test_the_record_exists_before_the_http_call(self):
        seen = []
        original = self.gateway.client.create_payout

        def spy(*a, **k):
            seen.append([r["state"] for r in self.wallet.payments_for(USER)])
            return original(*a, **k)
        self.turn("Send 50 to my daughter")
        with patch.object(self.gateway.client, "create_payout", side_effect=spy):
            self.sign()
        self.assertEqual(seen, [["SUBMITTING"]])

    def test_audit_has_no_emails_or_secrets_after_a_full_flow(self):
        self.turn("Send 50 to my daughter")
        self.sign()
        self.buy()
        text = self.audit.path.read_text(encoding="utf8")
        self.assertNotIn("anna.lee@family.example", text)
        self.assertNotIn("Authorization", text)
        self.assertNotIn("Bearer", text)
        self.assertIn("paypal.call", text)
        self.assertTrue(json.loads(text.splitlines()[-1]))
