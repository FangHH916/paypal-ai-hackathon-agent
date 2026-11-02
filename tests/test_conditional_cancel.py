"""A threat that says "if I don't pay" is not her cancelling anything.

Seen in an independent red-team pass: "... my benefits will be suspended if I don't pay 300 right now" was read as "don't
pay", so Star answered "OK, no payment will be sent" with no warning and no hotline, and the next "send 300 to Ben" got a
card. Now "don't send / don't pay" after "if", "unless" or "or" is a condition, not a cancel, and the scam scan runs
before any cancel answer, so a script is named even when the sentence also says "don't send".

The phrasings below were written for this test, not copied from the report.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402
from app.agent import fraud, request_guard  # noqa: E402
from app.kernel.hotline import hotline  # noqa: E402
from app.models import RiskLevel  # noqa: E402

THREATS = [
    "The pension office says if I don't send 250 they'll stop my checks",
    "A woman from the benefits department told me my pension will be cut unless I pay 400 today",
    "The man on the phone says the government will cancel my benefits if I do not transfer 600 now",
    "They said my Social Security checks stop on Friday if I don't wire 350",
]
CONDITIONS = THREATS + [
    "He said if I don't pay 80 the parcel goes back",
    "Unless I don't send it by noon, nothing happens",
]
CANCELS = [
    "Don't send anything to Ben today",
    "Please don't pay that",
    "Never mind",
    "Do not send it",
    "Cancel that",
]
BENIGN_BENEFITS = [
    "My pension checks will stop in June when I move abroad, send 20 to Anna",
    "Send 40 to Anna, her benefits office was closed today",
]


class TestConditionIsNotCancel(Base):
    def test_a_condition_is_not_a_cancel(self):
        for text in CONDITIONS:
            with self.subTest(text=text):
                self.assertFalse(request_guard.cancelled(text), text)

    def test_a_real_cancel_still_cancels(self):
        for text in CANCELS:
            with self.subTest(text=text):
                self.assertTrue(request_guard.cancelled(text), text)
                self.setUp()
                r = self.turn(text)
                self.assertIn("no payment will be sent", r.reply.lower())

    def test_a_benefits_threat_stops_with_the_hotline(self):
        for text in THREATS:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")
                self.assertIn(hotline()["spoken"], r.reply)
                r = self.turn("send 300 to Ben")
                self.assertIsNone(self.s.current_draft_id, f"{text!r} then a card: {r.reply}")

    def test_a_scam_with_a_dont_send_in_it_is_still_named(self):
        # The cancel answer comes after the scan: a scam sentence that also says "don't send" gets the warning
        for text in ("The IRS called and said I'll be arrested, they said don't send it to anyone but them, 900",):
            with self.subTest(text=text):
                r = self.turn(text)
                self.assertIn(hotline()["spoken"], r.reply, r.reply)

    def test_benign_benefit_words_are_not_stopped(self):
        for text in BENIGN_BENEFITS:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertNotEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")
                self.assertLess(fraud.scan(text)[0].rank, RiskLevel.HIGH.rank, (text, fraud.scan(text)))
