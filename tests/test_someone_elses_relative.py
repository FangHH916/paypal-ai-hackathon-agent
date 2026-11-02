"""Someone else's daughter, or "his account", is not her saved daughter.

Seen in an independent red-team pass: "... needs money for his daughter's surgery, send 700" and "The caller said to keep
this between us and not mention it to my daughter, send 700 to his account" both became a card for $700 to Anna Lee, her
daughter, with no warning. Now "his / her / their / <name>'s daughter" never picks her saved family by the relation word,
a payment "to his account" never lands on a relative only mentioned in passing, and being told to keep it between them is
a secrecy sign. Phrasings below are new; the look-alikes must still reach a card for the right person.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402
from app.agent import fraud  # noqa: E402
from app.agent.textparse import match_payees  # noqa: E402

NOT_HERS = [
    "Send 300 for his son's school fees",
    "Pay 150 toward their daughter's wedding",
    "My neighbour needs 90 for her grandson's medicine, send it",
    "The pastor's daughter needs 120, send it",
]
TO_HIS_ACCOUNT = [
    "He said my daughter would understand, send 500 to his account",
    "Don't worry about my son, just pay 400 to this account",
]
SECRECY = [
    "The man on the phone said to keep this between us, send 600",
    "He asked me not to mention it to my son, send 300 to him",
    "She told me to keep it quiet from the family and wire 450",
    "They said I mustn't tell anyone at the bank, transfer 900",
]
STILL_HERS = [
    ("Send Anna 50, she's my daughter", "Anna Lee"),
    ("Send 50 to my daughter for her son's party", "Anna Lee"),
    ("Send Anna 40 to her account", "Anna Lee"),
    ("Send 60 to my son", "David Lee"),
    ("Send 25 to my daughter's account", "Anna Lee"),
]


class TestSomeoneElsesRelative(Base):
    def payees(self):
        return self.wallet.payees(self.s.user_id)

    def test_someone_elses_relative_is_not_hers(self):
        for text in NOT_HERS + TO_HIS_ACCOUNT:
            with self.subTest(text=text):
                self.assertEqual(match_payees(text, self.payees()), [], text)
                self.setUp()
                self.turn(text)
                self.assertIsNone(self.s.current_draft_id, text)

    def test_her_own_relatives_still_match(self):
        for text, name in STILL_HERS:
            with self.subTest(text=text):
                self.assertEqual([p["name"] for p in match_payees(text, self.payees())], [name], text)
                self.setUp()
                r = self.turn(text)
                self.assertIn(r.ui["type"], ("confirm", "cooldown"), f"{text!r} -> {r.reply}")
                self.assertEqual(self.s.current_draft.payee_name, name)

    def test_being_told_to_keep_it_between_you_is_secrecy(self):
        for text in SECRECY:
            with self.subTest(text=text):
                self.assertIn("secrecy", fraud.scan(text)[2], text)
                self.setUp()
                r = self.turn(text)
                self.assertEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")

    def test_the_reported_sentences_get_no_card(self):
        for text in ("A sweet man I've been writing to on Words With Friends needs money for his daughter's surgery, send 700",
                     "The caller said to keep this between us and not mention it to my daughter, send 700 to his account"):
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")
                self.assertIsNone(self.s.current_draft_id)
