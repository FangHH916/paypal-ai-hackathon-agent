"""A "we'll cut off your power today unless you pay to this account" call is a scam warning, and a bill card whose amount
differs from what she said says so in words.

Seen in an independent red-team pass: "The power company says they'll cut off my electricity today unless I pay 240 to
this account" became a card for her saved $86.40 power bill, with no warning and no word that $86.40 is not $240.
Phrasings below are new; ordinary bill payments must not be stopped.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402
from app.agent import fraud  # noqa: E402
from app.kernel.hotline import hotline  # noqa: E402

SCRIPTS = [
    "A man from the electric company called, they'll disconnect me within the hour unless I pay 300",
    "The water company texted that my water gets shut off tonight, I have to send 180 to a new account",
    "Someone said my gas will be turned off today if I don't pay 150 right now",
]
ORDINARY = [
    "Pay my power bill before they cut it off",
    "Please pay the electric bill, I don't want it cut off",
]


NOTICE = ["The water company says the water will be shut off on Tuesday for repairs"]


class TestUtilityCutoff(Base):
    def test_a_planned_shutoff_notice_is_not_a_scam(self):
        for text in NOTICE:
            with self.subTest(text=text):
                self.assertNotIn("utility_threat", fraud.scan(text)[2], text)
                r = self.turn(text)
                self.assertNotEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")

    def test_a_cutoff_threat_stops_with_the_hotline(self):
        for text in SCRIPTS:
            with self.subTest(text=text):
                self.assertIn("utility_threat", fraud.scan(text)[2], text)
                self.setUp()
                r = self.turn(text)
                self.assertEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")
                self.assertIn(hotline()["spoken"], r.reply)

    def test_ordinary_bills_are_not_stopped(self):
        for text in ORDINARY:
            with self.subTest(text=text):
                self.assertNotIn("utility_threat", fraud.scan(text)[2], text)
                self.setUp()
                r = self.turn(text)
                self.assertIn(r.ui["type"], ("confirm", "cooldown"), f"{text!r} -> {r.reply}")

    def test_a_bill_card_says_when_the_amount_differs(self):
        for text, said in (("Pay 240 for my power bill", "$240.00"), ("Pay my water bill, it's 60", "$60.00")):
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertIn(r.ui["type"], ("confirm", "cooldown"), f"{text!r} -> {r.reply}")
                self.assertIn(said, r.reply, r.reply)
                self.assertIn("not", r.reply)

    def test_a_bill_card_with_the_right_amount_says_nothing_extra(self):
        r = self.turn("Pay my power bill of 86.40")
        self.assertNotIn(", not the", r.reply)
