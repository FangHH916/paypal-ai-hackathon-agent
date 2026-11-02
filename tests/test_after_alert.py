"""After a scam alert, what makes her safer still works.

Seen in an independent rescore: a payment card was open, then a scam script raised the session's alert, and "Stop that
payment" was refused as a new payment ("I can't prepare any payments in this conversation"), so the card stayed signable.
The alert stops new payments; cancelling, stopping, freezing, calling family or the hotline are never blocked by it.
"""
import os
from unittest.mock import patch

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from app.config import settings  # noqa: E402
from app.kernel.executor import KernelError  # noqa: E402
from app.kernel.hotline import hotline  # noqa: E402
from app.models import Phase, RiskLevel  # noqa: E402
from tests.paypal_helpers import USER, World  # noqa: E402

SCAM = "The police told me to move my savings to a safe account"
STOPS = ["Stop that payment", "Cancel that transfer", "Don't send it", "Stop the payment to Anna", "Please stop it",
         "Call it off", "Cancel the payment please"]
CANT = "can't prepare any payments"


class TestRiskReducingAfterAlert(World):
    def setUp(self):
        super().setUp()
        p = patch.object(settings, "velocity_max", 50)
        p.start()
        self.addCleanup(p.stop)

    def open_card_then_alert(self):
        self.turn("Send 50 to my daughter")
        d = self.s.current_draft
        self.assertEqual(self.s.phase, Phase.AWAITING_AUTH)
        self.turn(SCAM)
        self.assertGreaterEqual(self.s.scam_level.rank, RiskLevel.HIGH.rank)
        return d

    def test_stop_cancels_the_open_card_for_real(self):
        for text in STOPS:
            with self.subTest(text=text):
                self.setUp()
                d = self.open_card_then_alert()
                r = self.turn(text)
                self.assertNotIn(CANT, r.reply, text)
                self.assertNotIn(self.s.phase, (Phase.AWAITING_AUTH, Phase.COOLING), f"{text!r} left the card open: {r.reply}")
                self.assertIn("cancel", r.reply.lower())
                with self.assertRaises(KernelError):           # the card can no longer be signed
                    ch = self.kernel.issue_challenge(self.s, d.draft_id, d.digest, verify_answer=True)
                    tok = self.kernel.authorize(self.s, d.draft_id, d.digest, self.signature(ch), verify_answer=True)
                    self.kernel.commit(self.s, d.draft_id, tok)
                self.assertEqual(self.writes("/v1/payments/payouts"), [])

    def test_freeze_after_alert(self):
        self.open_card_then_alert()
        r = self.turn("Freeze everything")
        self.assertNotIn(CANT, r.reply)
        self.assertTrue(self.wallet.user(USER)["frozen"]["on"])

    def test_family_and_hotline_after_alert(self):
        for text in ("Call my daughter", "What is the scam hotline number?", "Please tell my family", "I want to talk to my son",
                     "Who can I call about a scam?"):
            with self.subTest(text=text):
                self.setUp()
                self.open_card_then_alert()
                r = self.turn(text)
                self.assertNotIn(CANT, r.reply, text)
                self.assertIn(hotline()["spoken"], r.reply, f"{text!r} -> {r.reply}")
                self.assertNotIn("What would you like to do?", r.reply)

    def test_stopping_a_sent_payment_after_alert(self):
        self.wallet.add_payee(USER, "Tom Reed", "tom.reed@mail.example", "friend")
        self.turn("Send 20 to Tom Reed")
        rec = self.rec(self.sign())
        self.assertEqual(rec["state"], "UNCLAIMED")
        self.turn(SCAM)
        r = self.turn("Stop that payment")
        self.assertNotIn(CANT, r.reply)
        d = self.s.current_draft
        self.assertEqual((d.kind, d.rail, d.amount_cents), ("refund", "payout_cancel", 2000))
        stop = self.rec(self.sign())
        self.assertEqual(stop["state"], "REFUNDED")

    def test_new_payments_are_still_refused(self):
        self.open_card_then_alert()
        self.turn("Cancel it")
        r = self.turn("Send 30 to my son")
        self.assertIn(CANT, r.reply)
        self.assertIsNone(self.s.current_draft_id if self.s.phase in (Phase.AWAITING_AUTH, Phase.COOLING) else None)
