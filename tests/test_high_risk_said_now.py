"""A high-risk scan that talks about money is named in the same turn, whatever the scam family.

Seen in an independent red-team pass: "A pop-up said my computer is infected and the Apple technician wants 400 to fix it"
scanned HIGH (tech support) but the answer was the general menu, with no warning and no hotline; she only learned of the
alert when her next payment was refused. Before, only the four named scripts were stopped without a "send" or "pay"
in the sentence. Phrasings below are new.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402
from app.agent import fraud  # noqa: E402
from app.kernel.hotline import hotline  # noqa: E402
from app.models import RiskLevel  # noqa: E402

HIGH_WITH_MONEY = [
    "A pop-up said my computer is infected and the Apple technician wants 400 to fix it",
    "A man from tech support says my laptop has a virus and it costs 250 to remove",
    "They want me to install AnyDesk and the fee is 99",
    "The caller said not to mention it to my son, it's only 350",
    "A refund agent said they refunded me too much and I owe 500",
]


class TestHighRiskSaidNow(Base):
    def test_named_with_the_hotline_in_the_same_turn(self):
        for text in HIGH_WITH_MONEY:
            with self.subTest(text=text):
                self.setUp()
                self.assertGreaterEqual(fraud.scan(text)[0].rank, RiskLevel.HIGH.rank, (text, fraud.scan(text)))
                r = self.turn(text)
                self.assertEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")
                self.assertIn(hotline()["spoken"], r.reply)
                self.assertNotIn("What would you like to do?", r.reply)
