"""Only HER lost phone or wallet brings the freeze question; someone else's, or one already found, does not.

Seen in an independent red-team pass: "My grandson Ben lost his phone at school, send him 30" and "I lost my phone last
week but found it, send 25 to Anna" got "I'm sorry about your phone. Would you like me to freeze..." and the payment was
dropped. Phrasings below are new; the lost-phone questions in tests/test_lost_phone.py still apply.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402

NOT_HERS = [
    "My granddaughter's phone was stolen at the mall, send Anna 35 for a new one",
    "David lost his wallet on the train, send him 60",
    "Sam told me his phone got stolen at football, pay Sam 45",
    "Anna misplaced her purse, send her 25 for the bus",
]
FOUND_OR_OLD = [
    "I lost my wallet yesterday but the neighbour found it, send 20 to Ben",
    "I thought I lost my phone but it turned up in my coat, send 30 to Anna",
    "Ever since I lost my old phone I use the tablet. Send 10 to Sam.",
]
STILL_ASKS = [
    "Someone took my handbag at the market, send 20 to Anna",
    "I've lost my wallet somewhere in town",
    "My purse has been stolen",
]


class TestOnlyHerLostDeviceAsks(Base):
    def test_someone_elses_lost_phone_is_a_normal_payment(self):
        for text in NOT_HERS + FOUND_OR_OLD:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertNotIn("freeze", r.reply.lower(), f"{text!r} -> {r.reply}")
                self.assertIn(r.ui["type"], ("confirm", "cooldown"), f"{text!r} -> {r.reply}")
                self.assertFalse(self.s.freeze_offer_pending)

    def test_her_own_lost_device_still_asks(self):
        for text in STILL_ASKS:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertIn("freeze", r.reply.lower(), f"{text!r} -> {r.reply}")
                self.assertTrue(self.s.freeze_offer_pending, text)
