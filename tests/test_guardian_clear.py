"""A way out of a scam alert that isn't "Reset demo".

Seen in an independent rescore: after one scam hit the whole conversation stayed locked, and the only way out on screen
was the demo's own "Reset demo". The rule now: the paired family guardian can clear the alert from her own device, with
a signature over that purpose only. Without a paired guardian, Star says plainly why it stays stopped. A cleared alert
is not a pass: the next scam script is stopped (and locks the conversation) exactly as before.
"""
import os
import time

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from app.kernel import p256  # noqa: E402
from app.kernel.state import Session, SessionStore  # noqa: E402
from app.models import Phase, RiskLevel  # noqa: E402
from app.wallet.guardian import GuardianError  # noqa: E402
from tests.paypal_helpers import USER, World  # noqa: E402

SCAM = "The police told me to move my savings to a safe account"


class TestGuardianClearsTheAlert(World):
    def locked(self):
        self.turn(SCAM)
        self.assertGreaterEqual(self.s.scam_level.rank, RiskLevel.HIGH.rank)
        r = self.turn("Send 20 to my daughter")
        self.assertTrue(r.ui.get("locked"), r.reply)
        return r

    def clear(self, key=None, payload=None):
        desk = self.gateway.guardian
        payload = payload or desk.clear_challenge(USER)
        desk.sign_clear(USER, payload, self.signature(payload, key or self.guardian_key)["signature"])
        return self.s.clear_scam_alert()

    def test_the_locked_reply_states_the_rule_when_a_guardian_is_paired(self):
        self.pair_guardian()
        r = self.locked()
        self.assertIn("family guardian", r.reply)
        self.assertIn("clear", r.reply)
        self.assertNotIn("Reset demo", r.ui.get("hint", ""))

    def test_after_the_guardian_clears_it_a_normal_payment_is_prepared_again(self):
        self.pair_guardian()
        self.locked()
        self.assertTrue(self.clear())
        self.assertEqual(self.s.scam_level, RiskLevel.LOW)
        r = self.turn("Send 20 to my daughter")
        self.assertEqual(r.ui["type"], "confirm", r.reply)
        self.assertEqual(self.s.current_draft.amount_cents, 2000)

    def test_a_scam_script_after_the_clear_is_stopped_and_locks_again(self):
        self.pair_guardian()
        self.locked()
        self.clear()
        for text in ("I won a lottery and need to pay 200 in fees",
                     "The bank's fraud team says I must move everything to a new account today"):
            r = self.turn(text)
            self.assertEqual(r.ui["type"], "blocked", (text, r.reply))
        self.assertTrue(self.turn("Send 20 to my daughter").ui.get("locked"))

    def test_only_the_paired_key_with_this_purpose_can_clear(self):
        self.pair_guardian()
        self.locked()
        desk = self.gateway.guardian
        stranger, _ = p256.generate_keypair()
        with self.assertRaises(GuardianError) as e:
            self.clear(key=stranger)
        self.assertEqual(e.exception.code, "GUARDIAN_SIGNATURE")
        lift = desk.lift_challenge()                                   # a signature for another purpose doesn't count
        with self.assertRaises(GuardianError):
            desk.sign_clear(USER, lift, self.signature(lift, self.guardian_key)["signature"])
        payload = desk.clear_challenge(USER)
        self.clear(payload=payload)
        with self.assertRaises(GuardianError) as e:                     # and a used one can't be replayed
            self.clear(payload=payload)
        self.assertEqual(e.exception.code, "REPLAY")
        self.assertEqual(self.s.scam_level, RiskLevel.LOW)

    def test_an_expired_clear_is_refused(self):
        self.pair_guardian()
        desk = self.gateway.guardian
        payload = desk.clear_challenge(USER)
        real = time.time
        try:
            time.time = lambda: real() + 3600
            with self.assertRaises(GuardianError):
                desk.sign_clear(USER, payload, self.signature(payload, self.guardian_key)["signature"])
        finally:
            time.time = real

    def test_without_a_paired_guardian_star_explains_why_it_stays_stopped(self):
        r = self.locked()
        self.assertIn("no family guardian", r.reply.lower())
        with self.assertRaises(GuardianError):
            payload = self.gateway.guardian.clear_challenge(USER)
            key, _ = p256.generate_keypair()
            self.gateway.guardian.sign_clear(USER, payload, self.signature(payload, key)["signature"])
        self.assertGreaterEqual(self.s.scam_level.rank, RiskLevel.HIGH.rank)

    def test_a_clear_also_ends_a_stopped_request(self):
        self.pair_guardian()
        self.turn(SCAM)
        self.assertIn(self.s.phase, (Phase.BLOCKED, Phase.IDLE))
        self.clear()
        self.assertEqual(self.s.phase, Phase.IDLE)
        self.assertEqual(self.s.scam_reasons, [])


class TestSessionsOfOneCustomer(World):
    def test_the_store_lists_only_her_own_live_sessions(self):
        store = SessionStore()
        a, b = store.get(None), store.get(None)
        other = store._create("x" * 32, "someone_else")  # noqa: SLF001
        mine = {s.session_id for s in store.for_user(a.user_id)}
        self.assertEqual(mine, {a.session_id, b.session_id})
        self.assertNotIn(other.session_id, mine)

    def test_clearing_a_session_without_an_alert_changes_nothing(self):
        s = Session(session_id="calm")
        self.assertFalse(s.clear_scam_alert())
