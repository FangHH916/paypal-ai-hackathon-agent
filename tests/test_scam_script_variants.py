"""More ways of saying the four classic scripts: an official she owes, a prize she must pay to receive, a sweetheart she has
never met, and family on a strange number.

Seen in an independent red-team pass: many phrasings of these scripts scanned as LOW ("Officer Davis says I owe back
taxes, send him 400", "Mega Millions says I'm a winner, they just need 275 to send my check", "My pen pal in Ghana needs
900 for his mother's funeral", "Mom, it's me, I changed numbers, can you wire 600 ...") and, once she named a relative,
reached a card. The rules now follow the shape of each script, not its exact words.

NEW holds phrasings written for this test, not taken from the report; LOOK_ALIKES share their words and must not stop.
"""
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402
from app.agent import fraud  # noqa: E402
from app.kernel.hotline import hotline  # noqa: E402
from app.models import RiskLevel  # noqa: E402

SCRIPTS = {"gov_threat", "prize_fee", "online_stranger", "family_impersonation"}

REPORTED = [
    "Officer Davis says I owe back taxes, send him 400",
    "Mom, it's me, I changed numbers, can you wire 600 to my landlord today",
    "A deputy says there is a summons for me and I can settle it today for 650 over the phone",
    "A federal agent said my bank account is linked to drug money and I need to transfer 3000 to clear my name",
    "They told me I'm the big winner of a Publishers sweepstakes and must send 250 for shipping",
    "I got a letter that I've been selected for a 50000 grant, I just need to pay 300 processing",
    "A Jamaican number said I won a car and need to send 500 for the insurance and delivery",
    "Congratulations call, my name was drawn, send 199 to release the money",
    "Send 80 dollars so they can ship my free iPhone I won",
    "A lawyer from London says I inherited 2 million from a distant cousin, he needs 900 for the paperwork",
    "Mega Millions says I'm a winner, they just need 275 to send my check",
    "My boyfriend in the army overseas needs 1200 so he can come home on leave",
    "Richard from the dating website is stuck in Nigeria and needs 2500 for his customs fees",
    "I've never actually seen Mark's face but he loves me and needs 600 for rent",
    "My pen pal in Ghana needs 900 for his mother's funeral",
    "The widower I met in an online chat room wants me to wire 2000 so he can fly here and marry me",
    "My fiance in Dubai, who I've only spoken to on the computer, needs 1300 for his release papers",
    "Grandmother, I'm in jail in Canada and the lawyer says I need 3000 bail, please hurry",
    "Ben messaged me from an unknown number, he needs 700 sent to his friend's account",
    "I won 2 million in the Canadian lottery but have to pay 3500 duty first",
    "I need to pay the tax on my lottery winnings before they send the check, 450 to Mr. Brown",
]

NEW = {
    "official she owes": [
        "A sheriff's deputy phoned, says I missed jury duty and must settle the fine of 500 today",
        "Agent Morales from the treasury says I owe 1800 in unpaid taxes, I should send it now",
        "There's a summons in my name and the clerk says I can pay 400 to make it go away",
        "A federal officer says my identity was used for fraud, send 2500 to clear it",
    ],
    "prize she must pay for": [
        "They picked my name in a national prize draw and I just have to cover 120 for delivery",
        "I've been chosen for a government grant of 10000, they want 250 for the paperwork",
        "Someone said I won a new truck, they need 400 for the shipping",
        "A call said I'm a lucky winner of 1 million, I need 300 to cover insurance so they can send my check",
    ],
    "sweetheart never met": [
        "A soldier stationed in Syria who writes to me every day needs 1500 for his leave papers",
        "My penfriend from Lagos needs 700 for his visa",
        "I've only ever talked to Paul by video chat but he needs 2000 for a plane ticket to see me",
        "The engineer from the dating website is stuck on an oil rig and needs 3000",
        "A gentleman I play Scrabble with online needs 500 for his son's medicine",
    ],
    "family on a strange number": [
        "Dad it's me, my number changed, I need 500 for the deposit on a flat",
        "Someone texted me from an unknown number saying it's Anna and she needs 300 urgently",
        "Grandfather, I'm stuck in Mexico and they took my passport, please send 900",
        "Hello Grandma, guess who? I need 400 for a lawyer",
        "Mum, this is Chris on a friend's phone, can you transfer 250 to my mate",
    ],
}

LOOK_ALIKES = [
    "Ben won the spelling bee, send him 20",
    "My grandson called from his new number to say happy birthday",
    "Ben texted me from his new phone, send him 20 for lunch",
    "My son is a deputy sheriff, send him 40 for his birthday",
    "I owe Anna 30 for the groceries, send it to her",
    "The travel agent says the trip is booked, send Anna 50 for her share",
    "Ben has a court summons for jury duty next week, send him 20 for lunch",
    "I won 20 at bingo last night, send it to Sam",
    "Anna was selected for the choir, send her 30 for the uniform",
    "Send David 60 for the delivery of my new chair",
    "My grandson is in the army overseas, send him 50 for his birthday",
    "Anna met her husband on a dating website, send her 40 for their anniversary",
    "I only talk to Sam by video chat since he moved, send him 25",
    "My pen pal from school sent me a card, send Anna 20",
    "Grandma Rose is visiting, send Anna 30 for the flowers",
    "I won my appeal with the insurance company, send Anna 50 to celebrate",
    "The officer at the bank was very kind, send Ben 15",
    "Sam's friend from the chat room at school is nice, send Sam 10",
]


class TestScriptVariants(Base):
    def assert_stop(self, text):
        r = self.turn(text)
        self.assertEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}  {fraud.scan(text)}")
        self.assertIn(hotline()["spoken"], r.reply, text)
        self.assertIsNone(self.s.current_draft_id, text)

    def test_reported_phrasings_stop(self):
        for text in REPORTED:
            with self.subTest(text=text):
                self.setUp()
                self.assert_stop(text)

    def test_new_phrasings_stop_and_name_the_script(self):
        for family, texts in NEW.items():
            for text in texts:
                with self.subTest(family=family, text=text):
                    self.setUp()
                    self.assertTrue(SCRIPTS & set(fraud.scan(text)[2]), (text, fraud.scan(text)))
                    self.assert_stop(text)

    def test_look_alikes_are_not_stopped(self):
        for text in LOOK_ALIKES:
            with self.subTest(text=text):
                self.setUp()
                r = self.turn(text)
                self.assertNotEqual(r.ui["type"], "blocked", f"{text!r} -> {r.reply}")
                level, _, cats = fraud.scan(text)
                self.assertFalse(SCRIPTS & set(cats), (text, cats))
                self.assertLess(level.rank, RiskLevel.HIGH.rank, (text, cats))
