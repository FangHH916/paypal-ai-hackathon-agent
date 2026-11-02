"""A word from a scam script in an ordinary sentence is not the script.

Seen in an independent red-team pass: payments to her saved family were stopped as scams, with the hotline, because the
sentence only mentioned the police, Medicare, a court, a prize, a fee or an inheritance ("My son David is a police officer,
send him 60 for dinner", "Ben won the raffle at church, send him 15 to cover the ticket fee"). Now an authority counts
only when it is the one asking her (it says, calls, wants, told her), a threat said in the negative ("he won't go to
jail") is no threat, and a prize or inheritance fee counts only when the prize or inheritance is said to be hers.

The reported sentences are kept as look-alikes, with new ones written for this test; the classic scripts in
tests/test_classic_scams.py must still stop.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402
from app.agent import fraud  # noqa: E402
from app.kernel.hotline import hotline  # noqa: E402
from app.models import RiskLevel  # noqa: E402

REPORTED = [
    "My son David is a police officer, send him 60 for dinner",
    "Ben said the police were very helpful when his bike was stolen, send him 30",
    "The police returned my lost purse, send 25 to Ben as thanks for driving me",
    "Pay David 120, he covered my Medicare co-pay last week",
    "David's court case is over and he won't go to jail, send him 100 to celebrate",
    "My grandson Sam won a prize at the science fair and the entry fee was 20, please pay him back",
    "Ben won the raffle at church, send him 15 to cover the ticket fee",
    "I'd like to send Anna 200, she inherited her father-in-law's house and needs help with the closing fees",
]
NEW = [
    "Send Anna 45, she paid my Social Security card replacement fee",
    "My neighbour used to be a police sergeant, send Ben 20 for mowing the lawn",
    "David won't go to prison for that parking mistake, the court dropped it, send him 50",
    "Anna inherited some furniture and needs 80 for the moving fee, send it to her",
    "Sam's team won the cup and the club fee is 30, send it to Sam",
    "The government sent my tax refund, send 40 of it to Ben",
]
STILL_SCAMS = [
    "The police called and said I'm under investigation, I have to send 900 to clear it",
    "A man from Medicare told me to pay 250 or my coverage ends",
    "I have won a lottery abroad and they need 400 for the release fee",
    "They said I inherited money from a relative overseas and need to pay 700 in fees to release it",
    "The court says I'll be arrested unless I pay 1200",
]


class TestScamWordsInOrdinarySentences(Base):
    def test_ordinary_payments_reach_a_card(self):
        for text in REPORTED + NEW:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertNotEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}  {fraud.scan(text)}")
                self.assertLess(self.s.scam_level.rank, RiskLevel.HIGH.rank, (text, fraud.scan(text)))

    def test_the_scripts_still_stop(self):
        for text in STILL_SCAMS:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}  {fraud.scan(text)}")
                self.assertIn(hotline()["spoken"], r.reply)
