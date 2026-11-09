"""Ordinary sentences that only sound like a script (Nov 9).

* "The IRS refund came in, how much did I spend this month?" was answered as a refund request ("I don't see a payment I can get
  back"). A "how much did I spend" question is a spending question, whatever else the sentence mentions.
* "My computer has a virus, can my son fix it?" scanned as a tech-support scam and marked the conversation, so her next
  payment to her daughter needed the guardian. A virus is the script only when someone else claims it (a caller, a pop-up,
  a technician) or money is wanted to fix it, inside the same sentence.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402

HER_OWN = [
    "My computer has a virus, can my son fix it?",
    "I think my laptop got a virus from that email, I'll ask David",
    "My phone was hacked last year so I changed my password",
]
SCRIPTS_NO_AMOUNT = [
    "A man on the phone told me my computer has a virus",
    "A pop-up said my laptop is infected",
]
SCRIPTS_WITH_MONEY = [
    "My laptop has a virus and the technician wants 300 to fix it",
    "Someone called to say my computer is hacked and I need to pay 200",
]


class TestSpendingQuestion(Base):
    def test_a_spending_question_mentioning_a_refund(self):
        for text in ("The IRS refund came in, how much did I spend this month?", "How much have I spent? My tax refund arrived"):
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertNotIn("get back", r.reply, r.reply)
                self.assertIn("spent", r.reply.lower(), r.reply)


class TestVirusWords(Base):
    def test_her_own_virus_is_not_a_scam_and_her_next_payment_is_normal(self):
        for text in HER_OWN:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertNotEqual(r.ui["type"], "blocked", r.reply)
                self.assertNotIn("scam", r.reply.lower(), r.reply)
                r = self.turn("Send 50 to my daughter")
                self.assertEqual(r.ui["type"], "confirm", r.reply)

    def test_someone_else_claiming_a_virus_still_gets_a_warning(self):
        for text in SCRIPTS_NO_AMOUNT:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertIn("scam", r.reply.lower(), r.reply)

    def test_money_to_fix_a_virus_is_stopped(self):
        for text in SCRIPTS_WITH_MONEY:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertEqual(r.ui["type"], "blocked", r.reply)
                self.assertIsNone(self.s.current_draft_id)
