"""A phone case or a glasses case in a payment is not a question about her PayPal cases.

Seen while re-checking a red-team finding: "My grandson Ben lost his phone at school, send him 30 for a new case" answered
"You have 1 PayPal case ..." and prepared nothing. The phrasings below are new.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402
from app.agent.llm import detect_intent  # noqa: E402

PAYMENTS = [
    "Send Anna 20 for a phone case",
    "Pay Sam 15 for a new case for his glasses",
    "Give David 25, he bought me a case for my tablet",
    "My grandson Ben lost his phone at school, send him 30 for a new case",
]
QUESTIONS = [
    "Do I have any PayPal cases?",
    "What's happening with my case?",
    "Is my dispute still open?",
    "Any open cases?",
]


class TestCaseWord(Base):
    def test_a_case_bought_is_a_payment(self):
        for text in PAYMENTS:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text), "transfer", text)
                self.setUp()
                r = self.turn(text)
                self.assertIn(r.ui["type"], ("confirm", "cooldown"), f"{text!r} -> {r.reply}")

    def test_her_cases_are_still_a_question(self):
        for text in QUESTIONS:
            with self.subTest(text=text):
                self.assertEqual(detect_intent(text), "disputes", text)
