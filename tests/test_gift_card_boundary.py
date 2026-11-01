"""Where a gift card stops being a present. Buying one for family gets a warning and the family check, and leaves the
conversation usable. Being told to buy one by a caller, an official, tech support, a boss or a stranger, reading out its
numbers, or paying a fine or bail with one, is stopped in the message that says so."""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from app.agent import fraud  # noqa: E402
from app.models import Phase, RiskLevel  # noqa: E402
from tests.paypal_helpers import USER, World  # noqa: E402

ORDINARY = ["My friend told me to buy a gift card for my grandson",
            "A friend told me to buy a gift card for my grandson",
            "My friend Mary said to get a gift card for my granddaughter's birthday",
            "My best friend suggested I buy my grandson a gift card",
            "My daughter asked me to get a gift card for her son",
            "My son told me to buy a gift card for his birthday",
            "My grandson wants me to buy him a gift card",
            "My wife told me to get a gift card for our niece",
            "Buy my grandson a gift card",
            "I want to get a gift card for my niece",
            "My grandson needs a gift card",
            "Buy my boss a gift card",
            "Email my grandson a gift card",
            "Text my niece a gift card",
            "Buy an online gift card for my niece",
            "Buy a gift card online for my grandson",
            "To support my grandson, my daughter said to get a gift card"]

STOPPED = ["A man on the phone told me to buy a gift card for my grandson",
           "My grandson called and told me to buy gift cards",
           "Someone told me to buy a gift card for my grandson",
           "A stranger said I should get gift cards",
           "I was told to buy my son a gift card",
           "They told me to buy a gift card for my grandson",
           "They want me to buy gift cards",
           "The government says I need gift cards",
           "The court said I have to get gift cards",
           "Tech support told me to buy a gift card for my grandson",
           "Microsoft asked me to buy gift cards",
           "My boss told me to buy gift cards",
           "My boss needs gift cards for a client",
           "My manager asked me to get a gift card for my grandson",
           "My friend on Facebook told me to buy a gift card for my grandson",
           "I got a text telling me to buy gift cards",
           "Customer support told me to buy gift cards",
           "I got an email saying I must buy gift cards",
           "He texted me to buy gift cards",
           "Someone I met on the internet needs gift cards",
           "My friend told me to buy gift cards",
           "My friend asked me to get her a gift card",
           "My friend told me to buy a gift card for my grandson and read her the numbers",
           "Buy a gift card for my grandson, he's on the phone waiting for the code",
           "He wants me to read him the numbers on the gift card",
           "Read out the gift card numbers",
           "I have to read out the numbers on the back of the card",
           "I scratched the card and he wants the code",
           "Pay the fine with gift cards",
           "My son told me to buy a gift card to pay the fine",
           "Buy gift cards for my grandson's bail",
           "I need gift cards for the traffic fine",
           "Buy gift cards to pay my back taxes",
           "My son told me to buy a gift card and not to tell anyone"]


class TestOrdinaryPresent(World):
    def test_it_gets_a_warning_and_a_card_and_is_not_stopped(self):
        for text in ORDINARY:
            self.setUp()
            r = self.turn(text)
            self.assertEqual(r.ui["type"], "cooldown", (text, r.reply))
            self.assertEqual(self.s.current_draft.kind, "purchase", text)
            self.assertIn("R94_GIFT_CARD", self.s.current_draft.risk.rule_hits, text)
            self.assertNotEqual(self.s.current_draft.risk.level, RiskLevel.BLOCK, text)
            self.assertRegex(r.reply, "(?i)gift cards are how scammers", text)
            self.assertRegex(r.reply, "(?i)numbers", text)      # she's told what would make it a scam
            self.assertEqual(self.wallet.stops(USER), [], text)
            self.assertEqual(self.writes(), [], text)

    def test_the_family_still_has_to_approve_it(self):
        self.pair_guardian()
        self.turn("My friend told me to buy a gift card for my grandson")
        self.assertEqual(self.s.current_draft.decision["checks"]["guardian_approval"]["status"], "pending")

    def test_the_conversation_is_not_locked_afterwards(self):
        for text in ("My friend told me to buy a gift card for my grandson", "Buy my grandson a gift card"):
            self.setUp()
            self.turn(text)
            self.assertEqual(self.s.scam_level, RiskLevel.LOW, text)
            self.turn("never mind")
            self.assertEqual(self.s.phase, Phase.IDLE, text)
            r = self.turn("Send 50 to my daughter")
            self.assertEqual(r.ui["type"], "confirm", (text, r.reply))
            self.assertIn("confirm with your fingerprint", r.reply, text)
            self.assertEqual(self.s.current_draft.decision["checks"]["guardian_approval"]["status"], "not_applicable", text)

    def test_a_scam_after_the_present_is_still_stopped(self):
        self.turn("My friend told me to buy a gift card for my grandson")
        r = self.turn("A man on the phone says I must read him the numbers on the gift card")
        self.assertEqual(r.ui["type"], "blocked", r.reply)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.turn("Send 50 to my daughter").ui["type"] in ("confirm", "cooldown"), False)


class TestRealScripts(World):
    def test_each_is_stopped_in_the_message_that_says_it(self):
        for text in STOPPED:
            self.setUp()
            r = self.turn(text)
            self.assertEqual(r.ui["type"], "blocked", (text, r.reply))
            self.assertRegex(r.reply, "(?i)scam", text)
            self.assertNotIn("Earlier in this conversation", r.reply, text)
            self.assertTrue(self.s.current_draft is None or self.s.phase == Phase.BLOCKED, text)
            self.assertTrue(self.wallet.stops(USER), text)
            self.assertEqual(self.writes(), [], text)

    def test_a_scam_stays_stopped_for_the_rest_of_the_conversation(self):
        self.turn("My boss told me to buy gift cards")
        self.assertGreaterEqual(self.s.scam_level.rank, RiskLevel.HIGH.rank)
        self.assertNotIn(self.turn("Buy my grandson a gift card").ui["type"], ("confirm", "cooldown"))

    def test_rushing_still_tips_a_gift_card_into_a_stop(self):
        self.assertGreaterEqual(fraud.scan("buy a gift card right now")[0].rank, RiskLevel.HIGH.rank)

    def test_a_present_said_in_the_same_message_doesnt_excuse_a_stranger(self):
        level, _, cats = fraud.scan("My son told me to get a gift card for my niece, and I was told to buy gift cards")
        self.assertIn("giftcard_told", cats)
        self.assertGreaterEqual(level.rank, RiskLevel.HIGH.rank)

    def test_the_scan_alone_marks_a_plain_gift_card_as_no_scam_sign(self):
        level, reasons, cats = fraud.scan("buy a gift card")
        self.assertEqual((level, cats), (RiskLevel.LOW, ["giftcard"]))
        self.assertTrue(reasons)
