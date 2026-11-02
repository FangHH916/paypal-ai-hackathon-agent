"""A lost phone is a reason to OFFER a freeze, not to freeze everything on the spot.

Seen in an independent rescore: "I lost my phone, can you send 20 to my son" froze all payments with no question, because
"lost ... phone" anywhere in a sentence meant "freeze". Now Star asks once; a plain yes freezes, a no doesn't, and
"freeze my payments" said outright still freezes straight away. A script where someone claims to be family on a lost
phone is a scam stop (tests/test_classic_scams.py).
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402

ASKS = [
    "I lost my phone, can you send 20 to my son",
    "My phone was stolen yesterday",
    "I think someone stole my wallet",
    "I can't find my phone anywhere, send 30 to Anna",
    "I left my phone on the bus and it's gone",
]


class TestLostPhoneAsksFirst(Base):
    def frozen(self):
        return self.wallet.user(self.s.user_id)["frozen"]["on"]

    def test_a_lost_phone_asks_before_freezing(self):
        for text in ASKS:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertFalse(self.frozen(), f"{text!r} froze without asking: {r.reply}")
                self.assertIsNone(self.s.current_draft_id, text)
                self.assertIn("freeze", r.reply.lower())
                self.assertIn("?", r.reply)
                self.assertNotEqual(r.ui["type"], "blocked")

    def test_yes_freezes(self):
        for yes in ("Yes", "yes please", "Yes, freeze it", "OK"):
            with self.subTest(yes=yes):
                self.setUp()
                self.turn("I lost my phone, can you send 20 to my son")
                r = self.turn(yes)
                self.assertTrue(self.frozen(), r.reply)
                self.assertIn("frozen", r.reply.lower())

    def test_no_does_not_freeze_and_sends_nothing(self):
        self.turn("I lost my phone, can you send 20 to my son")
        r = self.turn("No")
        self.assertFalse(self.frozen())
        self.assertIsNone(self.s.current_draft_id)
        self.assertIn("not", r.reply.lower())

    def test_something_else_is_a_new_request(self):
        self.turn("My phone was stolen yesterday")
        r = self.turn("Send 20 to my son")
        self.assertFalse(self.frozen())
        self.assertEqual(r.ui["type"], "confirm", r.reply)

    def test_asking_outright_still_freezes_at_once(self):
        for text in ("I lost my phone, please freeze my payments", "Freeze everything", "My phone was stolen, block all payments"):
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertTrue(self.frozen(), f"{text!r} -> {r.reply}")

    def test_a_lost_phone_accessory_is_not_a_lost_phone(self):
        for text in ("I lost my phone charger, send 20 to my son", "I lost my phone cover, send 15 to Anna"):
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertFalse(self.frozen())
                self.assertEqual(r.ui["type"], "confirm", f"{text!r} -> {r.reply}")
