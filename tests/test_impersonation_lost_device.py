"""Someone claiming to be family, on a lost or stolen phone or wallet, is a scam stop, not a freeze question.

Seen in an independent red-team pass: "Grandma it's <name>, ... lost my wallet, send 300" and "It's me <name>, my phone got
stolen, please send 350 to this new account" got "Would you like me to freeze all payments", with no scam warning, and
"no" left the alert at LOW. The phrasings below were written for this test; the look-alikes must not be stopped.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402
from app.agent import fraud  # noqa: E402
from app.kernel.hotline import hotline  # noqa: E402

SCRIPTS = [
    "Grandpa, this is Kevin, somebody stole my wallet on the train, can you send 400",
    "Nana it's Jess, my phone died and I'm stranded at the bus station, wire 250",
    "It's me Tommy, my phone was stolen so use this new account, 500 please",
    "Granny it's Chloe, I'm stuck abroad and my purse is gone, I need 600",
    "It's me, my mobile got smashed, send 300 to this number instead",
]
LOOK_ALIKES = [
    "I lost my phone last week but found it, send 25 to Anna",
    "It's me again, send 20 to Anna",
    "It's Ben's birthday, send him 20",
    "Grandma's old phone still works, send 15 to Sam",
    "My grandson Ben lost his phone at school, send him 30 for a new case",
]


class TestImpersonationOnALostDevice(Base):
    def test_the_script_stops_with_the_hotline(self):
        for text in SCRIPTS:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")
                self.assertIn(hotline()["spoken"], r.reply)
                self.assertFalse(self.s.freeze_offer_pending, text)
                self.assertIn("family_impersonation", fraud.scan(text)[2])

    def test_look_alikes_are_not_impersonation(self):
        for text in LOOK_ALIKES:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertNotEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")
                self.assertNotIn("family_impersonation", fraud.scan(text)[2], text)
