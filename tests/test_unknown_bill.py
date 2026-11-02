"""A bill she doesn't have is said plainly, not drafted as another bill and then frozen.

Seen in an independent red-team pass: "I need to pay my property tax bill to the county, can you help" drafted her Maple
County Water bill (the word "county" matched the water company's name), and the second reading then froze it with an
explanation she could not follow. Now a generic word in a company's name ("county", "company") picks no bill, and a bill
she names but doesn't have gets "I couldn't find your ... bill". Phrasings below are new.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402
from app.models import Phase  # noqa: E402

MISSING = [
    ("Pay the county for my property taxes bill", "property tax"),
    ("I want to pay my phone bill", "phone"),
    ("Please pay my car insurance bill", "insurance"),
]
KNOWN = [
    ("Pay my Maple County water bill", "Maple County Water"),
    ("Pay the water bill", "Maple County Water"),
    ("Pay the Riverside bill", "Riverside Power"),
]


class TestUnknownBill(Base):
    def test_a_missing_bill_is_said_plainly(self):
        for text, kind in MISSING:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertIsNone(self.s.current_draft_id, f"{text!r} -> {r.reply}")
                self.assertNotEqual(self.s.phase, Phase.FROZEN)
                self.assertIn("couldn't find", r.reply, r.reply)
                self.assertIn(kind, r.reply.lower(), r.reply)

    def test_known_bills_still_reach_a_card(self):
        for text, name in KNOWN:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertIn(r.ui["type"], ("confirm", "cooldown"), f"{text!r} -> {r.reply}")
                self.assertEqual(self.s.current_draft.payee_name, name)
