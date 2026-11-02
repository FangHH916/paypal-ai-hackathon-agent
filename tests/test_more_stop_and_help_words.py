"""More everyday ways to cancel a waiting card, ask for the hotline, or ask for her family.

Seen in an independent red-team pass: with a $40 card to Ben waiting, "Scrap that payment", "Undo the payment to Ben" and
"What number should I ring to report this?" got the general menu, and after a scam stop "Get Anna on the phone for me" did
too. Phrasings below are new, plus look-alikes that must not cancel or turn into the help answer.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402
from app.kernel.hotline import hotline  # noqa: E402
from app.models import Phase  # noqa: E402

CANCELS = ["Scrap it", "Undo that", "Take it back please", "Scrap the transfer", "Undo the payment"]
HOTLINE = ["Where do I report this?", "Who do I ring about this?", "What number do I call to report it?"]
FAMILY = ["Get my son on the line", "Can you get David on the phone", "Get my daughter on the phone please"]
NOT_CANCEL = ["Send 20 to Sam to undo his parking mistake"]


class TestMoreStopAndHelpWords(Base):
    def test_cancel_words_cancel_the_waiting_card(self):
        for text in CANCELS:
            with self.subTest(text=text):
                self.setUp()
                self.turn("Send 40 to Ben")
                self.assertEqual(self.s.phase, Phase.AWAITING_AUTH)
                r = self.turn(text)
                self.assertIn("cancelled", r.reply.lower(), f"{text!r} -> {r.reply}")
                self.assertNotEqual(self.s.phase, Phase.AWAITING_AUTH)

    def test_hotline_questions_get_the_hotline(self):
        for text in HOTLINE:
            with self.subTest(text=text):
                self.setUp()
                self.turn("Send 40 to Ben")
                r = self.turn(text)
                self.assertIn(hotline()["spoken"], r.reply, f"{text!r} -> {r.reply}")

    def test_asking_for_family_after_a_stop(self):
        for text in FAMILY:
            with self.subTest(text=text):
                self.setUp()
                self.turn("The IRS called, I owe 1500 or I'll be arrested, pay them")
                r = self.turn(text)
                self.assertIn("number you already have", r.reply, f"{text!r} -> {r.reply}")

    def test_look_alikes_are_not_a_cancel(self):
        for text in NOT_CANCEL:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertIn(r.ui["type"], ("confirm", "cooldown"), f"{text!r} -> {r.reply}")
