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
