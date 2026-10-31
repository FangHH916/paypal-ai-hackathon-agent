"""The simulator's own rules: duplicate ids, request-id replay, no capture before approval, refund limits, faults."""
import unittest

from app.paypal import calls, factory, ids
from app.paypal.config import PayPalConfig
from app.paypal.errors import PayPalError, PayPalUnknown
from app.paypal.simulator import Simulator

D = "ab" * 32
ITEMS = [{"name": "Monitor", "quantity": 1, "unit_cents": 3900}]


class Base(unittest.TestCase):
    def setUp(self):
        self.pp = factory.build(PayPalConfig())
        self.c, self.sim = self.pp.client, self.pp.simulator

    def order(self, digest=D):
        return self.c.create_order(digest, reference_id="r", items=ITEMS, currency="USD", description="x",
                                   return_url="https://s.example/r", cancel_url="https://s.example/c")

    def payout(self, digest=D, receiver="anna@family.example"):
        return self.c.create_payout(digest, amount_cents=500, currency="USD", receiver=receiver, note="n")


class TestAuth(Base):
    def test_needs_a_bearer_token(self):
        r = self.c.t.session.get("https://paypal-simulator.invalid/v2/checkout/orders/X")
        self.assertEqual(r.status_code, 401)

    def test_hostname_can_never_resolve(self):
        self.assertTrue(self.c.cfg.base_url.endswith(".invalid"))


class TestPayouts(Base):
    def test_family_succeeds_a_stranger_stays_unclaimed(self):
        fam = self.payout()["batch_header"]["payout_batch_id"]
        other = self.payout("cd" * 32, "someone@mail.example")["batch_header"]["payout_batch_id"]
        self.assertEqual(self.c.get_payout_batch(fam)["items"][0]["transaction_status"], "SUCCESS")
        self.assertEqual(self.c.get_payout_batch(other)["items"][0]["transaction_status"], "UNCLAIMED")

    def test_same_key_replays_the_same_batch(self):
        a = self.payout()["batch_header"]["payout_batch_id"]
        self.assertEqual(self.payout()["batch_header"]["payout_batch_id"], a)
        self.assertEqual(len(self.sim.st["batches"]), 1)

    def test_duplicate_id_with_another_key_is_refused_with_a_link(self):
        a = self.payout()["batch_header"]["payout_batch_id"]
        body = {"sender_batch_header": {"sender_batch_id": ids.sender_batch_id(D)},
                "items": [{"recipient_type": "EMAIL", "amount": {"currency": "USD", "value": "5.00"}, "receiver": "x@y.example"}]}
        with self.assertRaises(PayPalError) as e:
            self.c.t.request("payout_create", "POST", "/v1/payments/payouts", body=body, write=True, headers={"PayPal-Request-Id": "other"})
        self.assertIn(a, e.exception.links[0]["href"])
        self.assertEqual(len(self.sim.st["batches"]), 1)

    def test_settling_takes_time(self):
        pp = factory.build(PayPalConfig(), settle_seconds=3600)
        b = pp.client.create_payout(D, amount_cents=500, currency="USD", receiver="a@family.example", note="n")
        got = pp.client.get_payout_batch(b["batch_header"]["payout_batch_id"])
        self.assertEqual(got["batch_header"]["batch_status"], "PENDING")

    def test_only_unclaimed_can_be_cancelled(self):
        fam = self.payout()["batch_header"]["payout_batch_id"]
        item = self.c.get_payout_batch(fam)["items"][0]["payout_item_id"]
        with self.assertRaises(PayPalError):
            self.c.cancel_payout_item(item)
        other = self.payout("cd" * 32, "someone@mail.example")["batch_header"]["payout_batch_id"]
        item = self.c.get_payout_batch(other)["items"][0]["payout_item_id"]
        self.assertEqual(self.c.cancel_payout_item(item)["transaction_status"], "RETURNED")


class TestOrders(Base):
    def test_payment_source_gives_payer_action(self):
        o = self.order()
        self.assertEqual(o["status"], "PAYER_ACTION_REQUIRED")
        self.assertIn("payer-action", [l["rel"] for l in o["links"]])

    def test_request_id_replays_the_same_order(self):
        self.assertEqual(self.order()["id"], self.order()["id"])
        self.assertEqual(len(self.sim.st["orders"]), 1)

    def test_no_capture_before_approval(self):
        with self.assertRaises(PayPalError) as e:
            self.c.capture_order(self.order()["id"], D)
        self.assertTrue(e.exception.has_issue("ORDER_NOT_APPROVED"))

    def test_capture_after_approval_then_replay_and_second_capture(self):
        oid = self.order()["id"]
        self.assertEqual(self.sim.buyer_decide_order(oid, "approve"), "https://s.example/r")
        cap = self.c.capture_order(oid, D)
        self.assertEqual(cap["status"], "COMPLETED")
        self.assertEqual(self.c.capture_order(oid, D)["_status"], 200)      # same key: a replay
        self.assertEqual(len(self.sim.st["captures"]), 1)
        with self.assertRaises(PayPalError) as e:
            self.c.capture_order(oid, "cd" * 32)
        self.assertTrue(e.exception.has_issue("ORDER_ALREADY_CAPTURED"))

    def test_cancel_returns_the_cancel_url(self):
        self.assertEqual(self.sim.buyer_decide_order(self.order()["id"], "cancel"), "https://s.example/c")


class TestRefunds(Base):
    def captured(self):
        oid = self.order()["id"]
        self.sim.buyer_decide_order(oid, "approve")
        return self.c.capture_order(oid, D)["purchase_units"][0]["payments"]["captures"][0]["id"]

    def test_full_refund_and_limits(self):
        cap = self.captured()
        self.assertEqual(self.c.refund_capture(cap, D)["status"], "COMPLETED")
        self.assertEqual(self.c.get_capture(cap)["status"], "REFUNDED")
        with self.assertRaises(PayPalError):
            self.c.refund_capture(cap, "cd" * 32)

    def test_partial_then_too_much(self):
        cap = self.captured()
        self.c.refund_capture(cap, D, amount_cents=1000, currency="USD")
        self.assertEqual(self.c.get_capture(cap)["status"], "PARTIALLY_REFUNDED")
        with self.assertRaises(PayPalError):
            self.c.refund_capture(cap, "cd" * 32, amount_cents=3000, currency="USD")

    def test_same_key_replays_the_refund(self):
        cap = self.captured()
        self.assertEqual(self.c.refund_capture(cap, D)["id"], self.c.refund_capture(cap, D)["id"])
        self.assertEqual(len(self.sim.st["refunds"]), 1)


class TestFaults(Base):
    def test_timeout_after_commit_really_commits(self):
        self.sim.arm("*", "timeout_after_commit", op="payout_create")
        with self.assertRaises(PayPalUnknown):
            self.payout()
        self.assertEqual(len(self.sim.st["batches"]), 1)

    def test_timeout_before_commit_does_not(self):
        self.sim.arm("*", "timeout_before_commit", op="payout_create")
        with self.assertRaises(PayPalUnknown):
            self.payout()
        self.assertEqual(self.sim.st["batches"], {})

    def test_http_500(self):
        self.sim.arm("*", "http_500", op="payout_create")
        with self.assertRaises(PayPalUnknown):
            self.payout()
        self.assertEqual(self.sim.st["batches"], {})

    def test_faults_are_per_visitor(self):
        self.sim.arm("visitor-a", "timeout_before_commit", op="payout_create")
        with calls.scope("visitor-b"):
            self.payout()
        with calls.scope("visitor-a"), self.assertRaises(PayPalUnknown):
            self.payout("cd" * 32)

    def test_unknown_fault_refused(self):
        with self.assertRaises(ValueError):
            self.sim.arm("*", "make_it_up")

    def test_duplicate_and_forged_events_are_queued(self):
        oid = self.order()["id"]
        self.sim.arm("*", "duplicate_webhook")
        self.sim.buyer_decide_order(oid, "approve")
        raws = [e["raw"] for e in self.sim.due_events()]
        self.assertEqual(len(raws), 2)
        self.assertEqual(raws[0], raws[1])


class TestPersistence(unittest.TestCase):
    def test_orders_survive_a_restart(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.sqlite3")
            a = factory.build(PayPalConfig(), store_path=path)
            oid = a.client.create_order(D, reference_id="r", items=ITEMS, currency="USD", description="x",
                                        return_url="https://s/r", cancel_url="https://s/c")["id"]
            self.assertIn(oid, Simulator(path).st["orders"])


if __name__ == "__main__":
    unittest.main()
