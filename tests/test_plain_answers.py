"""Plain answers to plain questions, from an independent rescore of the keyless demo.

Each rule here is tested with wordings that were not in the defect report, and with look-alike sentences it must leave alone.
"""
import os
import time
from unittest.mock import patch

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from app.models import Phase  # noqa: E402
from tests.paypal_helpers import USER, World  # noqa: E402

# 2026-11-01 00:30 in Kiritimati (UTC+14), still 2026-10-31 in UTC and in New York
EDGE_OF_NOVEMBER = 1793442600.0     # 2026-10-31T10:30:00Z


class TestSpendingThisMonth(World):
    def spend(self, text, zone="Pacific/Kiritimati", now=EDGE_OF_NOVEMBER):
        self.s.timezone = zone
        with patch("time.time", return_value=now):
            return self.turn(text).reply

    def test_nothing_spent_this_month_is_said_plainly(self):
        for text in ("how much did I spend", "How much have I spent this month?", "What's my spending so far?"):
            with self.subTest(text=text):
                self.setUp()
                reply = self.spend(text)
                self.assertIn("November 2026", reply, reply)
                self.assertIn("haven't spent anything", reply, reply)
                self.assertNotIn("$0.00", reply)
                self.assertNotIn("October", reply)

    def test_the_month_is_the_customers_not_the_first_transaction(self):
        self.wallet.update_user(USER, lambda u: u["transactions"].insert(0, {
            "date": "2026-11-01", "desc": "Transfer to Anna Lee", "amount_cents": -2500, "payee_id": "p_daughter"}))
        reply = self.spend("how much did I spend")
        self.assertIn("November 2026", reply)
        self.assertIn("$25.00", reply)
        self.assertIn("1 payment", reply)

    def test_in_new_york_it_is_still_october(self):
        reply = self.spend("how much did I spend", zone="America/New_York")
        self.assertIn("October 2026", reply)
        self.assertIn("haven't spent anything", reply)


ONCE_ONLY = "can only send"


class TestRepeatingPaymentsAreNotSilentlyOneOff(World):
    def test_a_repeating_request_is_refused_plainly_and_nothing_is_drafted(self):
        for text in ("Send 50 to my daughter every month",          # the defect
                     "Pay Ben Lee 20 dollars every week",
                     "Set up a monthly payment of 100 to my son",
                     "Give my grandson Sam 10 dollars each Friday",
                     "Send Anna 30 monthly",
                     "Transfer 40 to David on the first of every month",
                     "Keep sending my daughter 25 every two weeks",
                     "Can you send 15 to Ben automatically from now on"):
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertIn(ONCE_ONLY, r.reply, f"{text!r} -> {r.reply}")
                self.assertIn("can't set up", r.reply)
                self.assertEqual(self.s.phase, Phase.IDLE, f"{text!r} drafted a card")
                self.assertIsNone(self.s.current_draft_id)

    def test_she_is_offered_the_one_off_payment_in_her_own_words(self):
        r = self.turn("Send 50 to my daughter every month")
        sends = [o["send"] for o in r.ui.get("options", [])]
        self.assertEqual(sends, ["Send 50 to my daughter"])
        r = self.turn(sends[0])
        self.assertEqual(self.s.phase, Phase.AWAITING_AUTH, r.reply)
        self.assertEqual(self.s.current_draft.amount_cents, 5000)

    def test_look_alikes_are_one_off_payments(self):
        for text, kind in (("Pay my monthly water bill", "bill"),
                           ("Pay the power bill that comes every month", "bill"),
                           ("I usually send Anna 50 every month, please send it now", "transfer"),
                           ("Send 50 to my daughter", "transfer"),
                           ("Send 50 to my daughter for her weekly groceries", "transfer")):
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertNotIn(ONCE_ONLY, r.reply, f"{text!r} -> {r.reply}")
                self.assertIsNotNone(self.s.current_draft, f"{text!r} -> {r.reply}")
                self.assertEqual(self.s.current_draft.kind, kind)


class TestHugeAmountsExplainTheLimit(World):
    def test_a_million_is_read_and_the_limit_explained(self):
        for text in ("Send 1,000,000 to my daughter",          # the defect
                     "Send $2,500,000 to Anna",
                     "Give my son one million dollars",
                     "Pay Ben Lee a million",
                     "Transfer 1,200,000 dollars to my grandson Sam"):
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertNotIn("How much would you like", r.reply, f"{text!r} -> {r.reply}")
                self.assertIn("$5,000.00", r.reply, f"{text!r} -> {r.reply}")       # the most Star handles in one payment
                self.assertEqual(self.s.phase, Phase.BLOCKED)

    def test_long_numbers_that_are_not_money_are_still_not_amounts(self):
        from app.agent.textparse import parse_amount
        self.assertIsNone(parse_amount("my account is 12345678"))
        self.assertIsNone(parse_amount("call me on 5551234567"))
        self.assertEqual(parse_amount("send 1,000 to anna"), 1000)
        self.assertEqual(parse_amount("one million two hundred thousand dollars"), 1_200_000)
        r = self.turn("Send 12345678 to my daughter")
        self.assertIn("too long to be an amount", r.reply)
