"""A warning is not a stop when nothing was asked for, and her own surprise is not a secret someone forced on her.

Seen in an independent red-team pass: "Remind me to call the IRS about my refund" answered "For your safety, I've stopped
this one. Someone ... asking you to send money ..." when no payment was asked for, and "Send Anna 100 but don't tell her
it's from me, it's a surprise" was stopped as "being told to keep it secret". Now a high-risk sentence with no payment in
it gets a plain warning and the hotline (nothing is said to be stopped), and secrecy counts only when someone else asks
for it. Phrasings below are new.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402
from app.agent import fraud  # noqa: E402
from app.kernel.hotline import hotline  # noqa: E402

WARN_ONLY = [
    "The police said I am suspected of money laundering",
    "Someone from the bank's fraud team rang me this morning",
    "A man on the phone told me my computer has a virus",
    "The police called me about a refund",
]
HER_SURPRISE = [
    "Send Anna 100 but don't tell her it's from me, it's a surprise",
    "Send Ben 40 for his birthday, it's a surprise so don't tell him",
    "Pay David 25, and don't tell him it was me",
    "Send Sam 30, it's a surprise so keep it a secret",
]
STILL_SECRECY = [
    "He said don't tell your family, send 500",
    "The caller told me to keep it a secret, it's a surprise for my grandson, send 300",
    "They asked me not to tell anyone and to wire 800 today",
]


class TestWarningWithoutAPayment(Base):
    def test_no_payment_asked_means_a_warning_not_a_stop(self):
        for text in WARN_ONLY:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertNotIn("stopped", r.reply.lower(), f"{text!r} -> {r.reply}")
                self.assertNotEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")
                self.assertIn(hotline()["spoken"], r.reply, f"{text!r} -> {r.reply}")
                self.assertIn("Nothing has been sent", r.reply)

    def test_her_own_surprise_reaches_a_card(self):
        for text in HER_SURPRISE:
            with self.subTest(text=text):
                self.assertNotIn("secrecy", fraud.scan(text)[2], text)
                self.setUp()
                r = self.turn(text)
                self.assertIn(r.ui["type"], ("confirm", "cooldown"), f"{text!r} -> {r.reply}")

    def test_secrecy_someone_else_asked_for_still_stops(self):
        for text in STILL_SECRECY:
            with self.subTest(text=text):
                self.assertIn("secrecy", fraud.scan(text)[2], text)
                self.setUp()
                r = self.turn(text)
                self.assertEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")
