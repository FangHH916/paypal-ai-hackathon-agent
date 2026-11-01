"""Regressions from the browser review: how people really say things. Amounts must never be guessed from a stray number,
the commonest gift card script must be stopped, and she can always back out of a question."""
import os
import unittest

os.environ.setdefault("RULE_FIXED_HOUR", "14")     # before anything imports app.config: results can't depend on the clock

from app.agent.presenter import build_card, receipt_text  # noqa: E402
from app.agent.textparse import parse_amount  # noqa: E402
from app.agent.verifier import Verifier  # noqa: E402
from app.models import Phase  # noqa: E402
from tests.paypal_helpers import USER, World  # noqa: E402


class TestAmountParsing(unittest.TestCase):
    def test_the_amount_next_to_the_verb_wins_over_a_stray_number(self):
        for text, want in [("Send twenty to my son for his 500 dollar rent", 20),
                           ("Send two hundred to my son for 3 months", 200),
                           ("Send 200 to my son for 3 months", 200),
                           ("My daughter's car is in the shop, room 2, send 5 to her", 5),
                           ("Pay my son 75 on October 3", 75),
                           ("Please send David 40 for the 2 kids", 40)]:
            self.assertEqual(parse_amount(text), want, text)

    def test_a_stated_unit_wins_when_nothing_is_next_to_a_verb(self):
        self.assertEqual(parse_amount("It's for the 3 kids, 60 dollars to my son"), 60)
        self.assertEqual(parse_amount("My son needs $250 by 5pm"), 250)

    def test_counts_times_and_rooms_are_never_amounts(self):
        for text in ("Send my son money for 3 months", "room 12 please pay my daughter", "pay my son at 5pm", "send it in 2 weeks"):
            self.assertIsNone(parse_amount(text), text)

    def test_two_candidates_with_no_clue_is_a_question_not_a_guess(self):
        for text in ("My son's rent is 500 and I want 20 sent to him", "it costs 40 or so, maybe 60 for my son"):
            self.assertIsNone(parse_amount(text), text)
        self.assertIsNone(parse_amount("the rent is 500 dollars and the deposit 900 dollars"))

    def test_what_already_worked_still_works(self):
        for text, want in [("Send 50 to my daughter", 50), ("Send $1,200.50 to my son", 1200.5), ("send a grand to Anna", 1000),
                           ("Send fifty dollars to my son", 50), ("Send twenty dollars and fifty cents to my son", 20.5),
                           ("Send 5k to my son", 5000), ("Send 200 to my daughter, no, 300", 300), ("half of 200", 100),
                           ("Send 200 to my son and 100 to my daughter", 200), ("Pay Tom Reed 450 dollars", 450),
                           ("Send 86 dollars and 40 cents to my son", 86.4), ("Send 50 cents to my son", 0.5), ("Send fifty cents to my son", 0.5),
                           ("two hundred and fifty", 250), ("a couple of hundred", 200), ("one fifty", 150)]:
            self.assertEqual(parse_amount(text), want, text)
        for text in ("Send money to my son", "20%", "Send two to my son", "account 12345678"):
            self.assertIsNone(parse_amount(text), text)


class TestAmountsInTheConversation(World):
    def test_the_card_shows_the_amount_she_meant(self):
        self.turn("Send two hundred to my son for 3 months")
        self.assertEqual(self.s.current_draft.amount_cents, 20000)

    def test_ambiguous_numbers_get_a_question_and_no_card(self):
        r = self.turn("Send my son money, the rent is 500 and the deposit is 900")
        self.assertIsNone(self.s.current_draft)
        self.assertIn("How much", r.reply)

    def test_a_number_too_long_to_be_an_amount_is_named_not_dropped(self):
        r = self.turn("Send 999999999 to my daughter")
        self.assertIsNone(self.s.current_draft)
        self.assertIn("too long", r.reply)


class TestOfflineCheckIsDescribedHonestly(World):
    def test_the_card_says_the_offline_reader_shares_the_parser(self):
        self.turn("Send 50 to my daughter")
        d = self.s.current_draft
        card = build_card(d, self.wallet, Phase.AWAITING_AUTH, mode_label="SIMULATED")
        self.assertTrue(card["verification"]["independent"] is False)
        self.assertEqual(Verifier(None, self.wallet).offline, True)


class TestGiftCardScripts(World):
    SCRIPTS = ["A man on the phone says I must buy gift cards",
               "Someone called and told me to buy gift cards",
               "The caller says I have to get gift cards for him",
               "I was told to buy gift cards and read out the numbers",
               "I was told to buy gift cards",
               "a lady on the phone wants me to buy 3 gift cards",
               "The caller wants 500 in gift cards",
               "A lady from Microsoft asked me to get gift cards"]

    def test_being_told_to_buy_is_stopped_with_no_card_to_sign(self):
        for text in self.SCRIPTS:
            self.setUp()
            r = self.turn(text)
            self.assertTrue(self.s.current_draft is None or self.s.phase == Phase.BLOCKED, text)
            self.assertEqual(r.ui["type"], "blocked", (text, r.reply))
            self.assertRegex(r.reply, "(?i)scam")
            self.assertNotIn("Earlier in this conversation", r.reply, text)   # it was this message, and it counts as a stop
            self.assertTrue(self.wallet.stops(USER), text)
            self.assertEqual(self.writes(), [])

    def test_her_own_gift_card_still_goes_to_the_family(self):
        self.pair_guardian()
        self.turn("Buy my grandson a gift card")
        d = self.s.current_draft
        self.assertIsNotNone(d)
        self.assertNotEqual(self.s.phase, Phase.BLOCKED)
        self.assertEqual(d.decision["checks"]["guardian_approval"]["status"], "pending")


class TestPurchaseAmountSheNamed(World):
    def test_a_price_that_is_not_the_offer_never_reaches_a_card_to_sign(self):
        for text in ("Buy a 50 dollar gift card", "Buy my grandson a gift card for 50 dollars"):
            self.setUp()
            r = self.turn(text)
            self.assertEqual(self.s.phase, Phase.IDLE if self.s.current_draft is None else Phase.FROZEN, (text, r.reply))
            self.assertIn("match", r.reply, text)
            self.assertEqual(self.writes(), [])

    def test_the_offer_she_actually_named_still_works(self):
        self.turn("Buy a 500 dollar gift card")
        self.assertEqual(self.s.current_draft.amount_cents, 50000)
        self.assertNotEqual(self.s.phase, Phase.FROZEN)


class TestBackingOut(World):
    def test_cancel_and_never_mind_end_the_question(self):
        for word in ("cancel", "never mind", "Nevermind.", "forget it", "cancel please"):
            self.setUp()
            self.turn("pay my son")
            r = self.turn(word)
            self.assertIn("no payment will be sent", r.reply, word)
            self.assertNotIn("How much", r.reply)
            again = self.turn("what can you do")          # the unanswered question is gone
            self.assertNotIn("How much", again.reply)

    def test_cancel_with_a_card_open_still_cancels_it(self):
        self.turn("Send 50 to my daughter")
        self.turn("never mind")
        self.assertEqual(self.s.phase, Phase.IDLE)
        self.assertEqual(self.writes(), [])


class TestWaitingForFamilyIsNotADeadEnd(World):
    def setUp(self):
        super().setUp()
        self.wallet.add_payee(USER, "Tom Reed", "tom.reed@family.example", "friend")

    def test_unpaired_says_what_to_do_and_never_says_confirm(self):
        r = self.turn("Pay Tom Reed 400 dollars")
        self.assertNotIn("confirm with your fingerprint", r.reply)
        self.assertIn("Family page", r.reply)
        self.assertFalse(self.wallet.user(USER)["guardian"]["status"] == "paired")

    def test_paired_says_who_it_waits_for(self):
        self.pair_guardian()
        r = self.turn("Pay Tom Reed 400 dollars")
        self.assertNotIn("confirm with your fingerprint", r.reply)
        self.assertIn("Anna", r.reply)

    def test_an_ordinary_card_still_asks_for_the_fingerprint(self):
        r = self.turn("Send 50 to my daughter")
        self.assertIn("confirm with your fingerprint", r.reply)


class TestSimulatorWordsAreHonest(World):
    def receipt(self, state, mode="SIMULATED"):
        return {"state": state, "verified": True, "paypal_mode": mode}

    def test_simulated_mode_does_not_say_paypal_confirms(self):
        self.turn("Send 50 to my daughter")
        d = self.s.current_draft
        for state in ("SENT", "PAID"):
            text = receipt_text(d, self.receipt(state))
            self.assertNotIn("PayPal confirms", text)
            self.assertIn("simulator", text.lower())
        self.assertIn("PayPal confirms", receipt_text(d, self.receipt("SENT", "SANDBOX")))


if __name__ == "__main__":
    unittest.main()
