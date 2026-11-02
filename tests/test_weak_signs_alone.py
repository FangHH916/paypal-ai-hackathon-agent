"""A weak sign on its own (a prize, a lottery, "online friend") does not hold a small payment to her family for a guardian.

Seen in an independent red-team pass: "Anna won a prize at work, send her 50 to celebrate" and "Send 60 to Anna to cover
the processing fee for my passport she paid" scanned MEDIUM, so the card said it needed her family's OK, and with no
guardian set up it could not be sent at all. Now prize and romance words count only together with someone asking for the
money ("he needs 400"), or as part of a named script. Phrasings below are new.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402
from app.agent import fraud  # noqa: E402
from app.models import RiskLevel  # noqa: E402

ALONE = [
    "Ben's team won a prize at the fair, send him 20",
    "Send Sam 25 for the lottery scratch cards he got me",
    "Anna paid the processing fee for my visa renewal, send her 75",
    "My online friend group from church meets on Tuesdays, send Anna 20 for the cake",
]
WITH_A_DEMAND = [
    "My online friend needs 400 for his flight",
    "Sam says a lottery called him and he needs 200 for it",
]


class TestWeakSignsAlone(Base):
    def test_a_weak_sign_alone_reaches_a_plain_card(self):
        for text in ALONE:
            with self.subTest(text=text):
                self.assertEqual(fraud.scan(text)[0], RiskLevel.LOW, (text, fraud.scan(text)))
                self.setUp()
                r = self.turn(text)
                self.assertIn(r.ui["type"], ("confirm", "cooldown"), f"{text!r} -> {r.reply}")
                self.assertNotIn("family's OK", r.reply)

    def test_a_weak_sign_with_someone_asking_is_still_high(self):
        for text in WITH_A_DEMAND:
            with self.subTest(text=text):
                self.assertGreaterEqual(fraud.scan(text)[0].rank, RiskLevel.HIGH.rank, (text, fraud.scan(text)))
