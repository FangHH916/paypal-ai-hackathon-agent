"""In the simulator, nothing on screen says PayPal did something.

Seen in an independent rescore (simulator mode): the card said "After you confirm, PayPal sends it" and the Activity grid
said "PayPal item status SUCCESS". The words now follow the mode: in the simulator it is the simulator that sends and
answers; in the sandbox it is PayPal.
"""
import base64
import os
import re
import time
import unittest

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from starlette.testclient import TestClient  # noqa: E402

from app import runtime, security  # noqa: E402
from app.agent import presenter  # noqa: E402
from app.kernel import p256  # noqa: E402
from app.server import app  # noqa: E402
from tests.paypal_helpers import World  # noqa: E402

KINDS = ["Send 20 to my son", "I want my money back", "Ask David to pay me back 20", "Pay the Riverside Power bill"]


class TestCardWords(World):
    def test_the_next_step_names_whoever_really_answers(self):
        self.turn("Send 20 to my son")
        d = self.s.current_draft
        sim = presenter.build_card(d, self.wallet, self.s.phase, mode_label="SIMULATED")["next_step_words"]
        sandbox = presenter.build_card(d, self.wallet, self.s.phase, mode_label="SANDBOX")["next_step_words"]
        self.assertNotIn("PayPal", sim)
        self.assertIn("simulator", sim)
        self.assertIn("PayPal sends it", sandbox)

    def test_every_kind_of_card_in_the_simulator(self):
        for kind in ("purchase", "bill", "transfer", "refund", "money_request"):
            words = presenter.source_words(presenter._next_step_words(type("D", (), {"kind": kind})()), "SIMULATED")  # noqa: SLF001
            self.assertNotIn("PayPal", words, kind)
            self.assertRegex(words, r"^After you confirm, ", kind)


class TestServerWords(unittest.TestCase):
    """The card, the grid's reasons and a payment's timeline as the server sends them, in simulator mode."""

    def setUp(self):
        self.client = TestClient(app, base_url="http://localhost")
        self.addCleanup(self.client.close)
        security._hits.clear()
        self.s = runtime.sessions.new_for_visitor("abcdef0123456780")
        self.key, jwk = p256.generate_keypair()
        runtime.kernel.register_key(self.s, jwk)
        self.assertEqual(runtime.execution_gateway.mode_status()["label"], "SIMULATED")

    def test_card_grid_and_timeline_never_say_paypal_did_it(self):
        r = runtime.agent.run_turn(self.s, "Send 20 to my son")
        self.assertNotIn("PayPal", r.ui["card"]["next_step_words"])
        d = self.s.current_draft
        ch = runtime.kernel.issue_challenge(self.s, d.draft_id, d.digest)
        sig = base64.b64encode(p256.sign(self.key, ch.encode())).decode()
        tok = runtime.kernel.authorize(self.s, d.draft_id, d.digest, {"payload": ch, "signature": sig})
        receipt = runtime.kernel.commit(self.s, d.draft_id, tok, return_base="http://localhost")
        self.s.settle()
        for _ in range(40):                  # the simulator settles a payout after a moment, like PayPal
            runtime.execution_gateway.reconciler.pump()
            if runtime.wallet.payment(receipt["payment_id"])["verified"]:
                break
            time.sleep(0.1)
        rows = self.client.get("/api/activity", params={"session_id": self.s.session_id}).json()["rows"]
        self.assertTrue(rows)
        whys = [row["why"] for row in rows]
        self.assertTrue(any(w for w in whys), whys)
        for w in whys:
            self.assertNotRegex(w, r"\bPayPal\b", w)
        pay = self.client.get(f"/api/payments/{self.s.session_id}/{receipt['payment_id']}").json()["payment"]
        notes = [e.get("note", "") for e in pay["evidence"]]
        for n in notes:
            self.assertNotRegex(n, r"\bPayPal\b", n)


class TestStatusWords(unittest.TestCase):
    def test_status_words_switch_with_the_mode(self):
        js = open(os.path.join(os.path.dirname(__file__), "..", "web", "common.js"), encoding="utf-8").read()
        self.assertTrue(re.search(r"export function statusInfo\(status, verified = false, label = ''\)", js), "statusInfo takes the mode")
        self.assertTrue(re.search(r"statusInfo\(status, verified, label\)", js), "ppBadge passes the mode")
        for name in ("activity.js", "app.js"):
            src = open(os.path.join(os.path.dirname(__file__), "..", "web", name), encoding="utf-8").read()
            for call in re.findall(r"statusInfo\(([^()]*(?:\([^()]*\))?[^()]*)\)", src):
                self.assertEqual(call.count(","), 2, f"{name}: statusInfo({call}) doesn't pass the mode")


if __name__ == "__main__":
    unittest.main()
