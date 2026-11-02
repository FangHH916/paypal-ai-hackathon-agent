"""Classic elder-scam scripts stop before any card, with the hotline, and their look-alikes don't.

Seen in an independent rescore (fresh session, keyless): "The IRS called, I owe 1500 or I'll be arrested, pay them" got
"I heard two possible amounts" (there is one amount); "I won a lottery and need to pay 200 in fees" and "A nice young man I
met online needs 800 dollars for his plane ticket, send it" were asked "Who would you like to send money to?"; and
"Grandma it's me Ben, I lost my phone, wire me 900" froze every payment instead of naming the scam.

Each family is tested with phrasings written for this test, not taken from the report, and with benign sentences
that share its words and must still reach a normal card.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402
from app.agent import fraud  # noqa: E402
from app.agent.request_guard import problem  # noqa: E402
from app.kernel.hotline import hotline  # noqa: E402
from app.models import RiskLevel  # noqa: E402

REPORTED = [
    "The IRS called, I owe 1500 or I'll be arrested, pay them",
    "I won a lottery and need to pay 200 in fees",
    "A nice young man I met online needs 800 dollars for his plane ticket, send it",
    "Grandma it's me Ben, I lost my phone, wire me 900",
]

NEW = {
    "government threat": [
        "A man from the tax office says I have unpaid taxes and the sheriff will come to arrest me unless I send 2000 today",
        "Social Security said my benefits will be suspended unless I pay 300 now",
        "Someone from immigration says I'll be deported if I don't wire 1000",
        "The court officer says there is a warrant for me, transfer 700 to clear it",
        "They said I'll go to jail unless I pay the 450 I owe",
    ],
    "prize fee": [
        "They told me I'm a sweepstakes winner but I have to pay 150 for processing first",
        "To claim my jackpot I need to send 99 for the release fee",
        "I have an inheritance waiting overseas and must pay 400 in clearance charges to release it",
        "Pay 250 so they can unlock my lotto winnings",
    ],
    "romance or online stranger": [
        "My boyfriend from the dating app is stuck at the airport, send him 600",
        "A gentleman I've been chatting with on Facebook needs 1200 for customs, wire it",
        "Someone I have never met in person asked me for 500 for his hospital bill, pay it",
        "The lady I talk to on WhatsApp wants 300 for her visa, transfer it please",
    ],
    "family impersonation": [
        "Hi Grandpa, this is your granddaughter, my phone broke, please send 700",
        "Nana it's me, I'm using a friend's phone, can you wire 500",
        "My grandson texted from a new number saying he needs 400, send it to him",
        "Granny it's me Sam, I'm in trouble, transfer 350 to this account",
    ],
}

BENIGN = [
    "My grandson won a prize at school, send him 25 to celebrate",
    "I won 50 at bingo, send 20 of it to Anna",
    "My son met his wife online, send him 100 for their anniversary",
    "Send 40 to my daughter for her online course",
    "My son got a new phone, send him 50 for the case",
    "It's me again, send 20 to Anna",
    "Grandma's birthday is on Sunday, send 30 to Ben",
    "Send 60 to Anna, she is helping me with my tax return",
]


class TestClassicScamScriptsStop(Base):
    def assert_scam_stop(self, text):
        r = self.turn(text)
        self.assertEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")
        self.assertIn(hotline()["spoken"], r.reply, text)
        self.assertIsNone(self.s.current_draft_id, text)
        self.assertFalse(self.wallet.user(self.s.user_id)["frozen"]["on"], f"{text!r} froze payments instead")
        self.assertNotIn("two possible amounts", r.reply)
        self.assertNotIn("Who would you like", r.reply)

    def test_reported_scripts_stop_with_the_hotline(self):
        for text in REPORTED:
            with self.subTest(text=text):
                self.setUp()
                self.assert_scam_stop(text)

    def test_new_phrasings_of_each_family_stop(self):
        for family, texts in NEW.items():
            for text in texts:
                with self.subTest(family=family, text=text):
                    self.setUp()
                    self.assert_scam_stop(text)

    def test_each_family_alone_is_high_risk(self):
        for family, texts in NEW.items():
            for text in texts:
                with self.subTest(family=family, text=text):
                    level, _, cats = fraud.scan(text)
                    self.assertGreaterEqual(level.rank, RiskLevel.HIGH.rank, (text, cats))

    def test_look_alikes_reach_a_normal_card(self):
        for text in BENIGN:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertNotEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")
                _, _, cats = fraud.scan(text)
                self.assertFalse({"gov_threat", "prize_fee", "online_stranger", "family_impersonation"} & set(cats), (text, cats))


# Found while re-checking with new phrasings: a script that only says what the money is "needed" for, with no "send" or
# "pay" in it, got the generic menu instead of the stop.
NO_VERB = [
    "Granny it's me Tom, I'm in trouble and need 700 for bail, don't tell mom",
    "Grandpa it's me, I had an accident and I need 1200 for the hospital",
    "The sheriff says I owe 800 in back taxes or I'll be arrested",
    "A man I met on a dating site needs 2000 for his customs fees",
    "I won the lottery and there's a 300 release fee",
]
NO_VERB_BENIGN = [
    "I owe my daughter 40 for the groceries",
    "My grandson needs 50 for his school trip",
    "Anna needs 30 for the bus",
    "The tax office sent my refund of 300",
    "Our church raffle prize was a cake",
]


class TestScriptsWithoutASendWord(Base):
    def test_they_stop_with_the_hotline(self):
        for text in NO_VERB:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")
                self.assertIn(hotline()["spoken"], r.reply, text)

    def test_ordinary_needs_are_not_stopped(self):
        for text in NO_VERB_BENIGN:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertNotEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")
                self.assertLess(self.s.scam_level.rank, RiskLevel.HIGH.rank, text)


class TestOneAmountIsNotTwo(Base):
    def test_or_before_a_word_is_not_a_second_amount(self):
        for text in ("I owe 1500 or I'll be arrested", "Send 50 or else he gets upset"):
            with self.subTest(text=text):
                self.assertNotEqual(problem(text), "I heard two possible amounts. Which exact amount would you like to use?")

    def test_two_amounts_still_ask(self):
        for text in ("Send fifty or sixty to my son", "Send 50 or 60 to my son", "Send twenty or thirty to Anna"):
            with self.subTest(text=text):
                self.assertIsNotNone(problem(text))
