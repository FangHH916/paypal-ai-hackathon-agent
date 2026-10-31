"""Webhooks: nothing changes until PayPal verifies the signature. Dedup, out-of-order, amounts, terminal states."""
import json

from tests.paypal_helpers import USER, StubAdapter, World, sandbox_cfg


class Base(World):
    settle = 3600.0      # PayPal is "busy", so only events move things

    def deliver(self):
        return [self.gateway.webhooks.handle(e["raw"], e["headers"]) for e in self.sim.due_events()]

    def release_batches(self):
        for b in self.sim.st["batches"].values():
            b["_settle_at"] = 0

    def send(self):
        self.turn("Send 50 to my daughter")
        return self.rec(self.sign())


class TestSignature(Base):
    def test_a_real_event_moves_it_to_verified_sent(self):
        rec = self.send()
        self.assertEqual((rec["state"], rec["verified"]), ("PROCESSING", False))
        self.release_batches()
        results = self.deliver()
        self.assertEqual({r.code for r in results}, {"OK"})
        self.assertEqual((rec["state"], rec["verified"]), ("SENT", True))
        self.assertTrue(rec["verified_by"].startswith("webhook:WH-"))
        self.assertTrue(rec["event_ids"])

    def test_verify_goes_through_paypals_endpoint(self):
        rec = self.send()
        self.release_batches()
        self.deliver()
        calls = [r for r in self.sim.requests_seen if r["path"] == "/v1/notifications/verify-webhook-signature"]
        self.assertTrue(calls)
        self.assertEqual(rec["state"], "SENT")
        self.assertEqual(json.loads(calls[0]["body"])["webhook_id"], "SIMULATORWEBHOOKID")

    def test_the_verify_call_carries_the_raw_event_byte_for_byte(self):
        self.send()
        self.release_batches()
        raw = self.sim.due_events()[0]
        self.gateway.webhooks.handle(raw["raw"], raw["headers"])
        body = [r for r in self.sim.requests_seen if r["path"] == "/v1/notifications/verify-webhook-signature"][0]["body"]
        self.assertIn(b'"webhook_event":' + raw["raw"] + b"}", body)

    def test_forged_signature_changes_nothing(self):
        rec = self.send()
        self.release_batches()
        e = next(x for x in self.sim.due_events() if b"PAYOUTS-ITEM" in x["raw"])
        res = self.gateway.webhooks.handle(e["raw"], {**e["headers"], "paypal-transmission-sig": "Zm9yZ2Vk"})
        self.assertEqual((res.status, res.code), (400, "BAD_SIGNATURE"))
        self.assertEqual(rec["state"], "PROCESSING")

    def test_altered_body_fails(self):
        rec = self.send()
        self.release_batches()
        e = next(x for x in self.sim.due_events() if b"PAYOUTS-ITEM" in x["raw"])
        event = json.loads(e["raw"])
        event["resource"]["transaction_status"] = "REVERSED"
        res = self.gateway.webhooks.handle(json.dumps(event).encode(), e["headers"])
        self.assertEqual(res.code, "BAD_SIGNATURE")
        self.assertEqual(rec["state"], "PROCESSING")

    def test_forged_webhook_fault_is_rejected_in_the_pump(self):
        rec = self.send()
        self.sim.arm("*", "forged_webhook")
        self.release_batches()
        results = self.deliver()
        self.assertIn("BAD_SIGNATURE", {r.code for r in results})
        self.assertEqual(rec["state"], "SENT")               # the real ones still went through
        entries = [json.loads(line) for line in self.audit.path.read_text(encoding="utf8").splitlines()]
        self.assertTrue(any(x["event"] == "paypal.webhook_rejected" for x in entries))

    def test_missing_headers_400(self):
        res = self.gateway.webhooks.handle(b'{"id":"a","event_type":"x"}', {"paypal-transmission-id": "x"})
        self.assertEqual((res.status, res.code), (400, "MISSING_HEADERS"))

    def test_bad_json_400(self):
        hdr = {k: "x" for k in ("paypal-transmission-id", "paypal-transmission-time", "paypal-transmission-sig", "paypal-cert-url", "paypal-auth-algo")}
        self.assertEqual(self.gateway.webhooks.handle(b"not json", hdr).code, "BAD_BODY")

    def test_unknown_resource_is_acknowledged_and_logged(self):
        event = {"id": "WH-X", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "NOPE", "amount": {"value": "1.00"}}}
        raw = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        res = self.gateway.webhooks.handle(raw, self.sim._sign(event))
        self.assertEqual((res.status, res.code), (200, "UNMATCHED"))


class TestNotConfigured(World):
    HDR = {k: "x" for k in ("paypal-transmission-id", "paypal-transmission-time", "paypal-transmission-sig", "paypal-cert-url", "paypal-auth-algo")}

    def handler(self, adapter, cfg):
        from app.paypal.client import PayPalClient
        from app.paypal.reconcile import Reconciler
        from app.paypal.transport import PayPalTransport
        from app.paypal.webhooks import WebhookHandler
        client = PayPalClient(PayPalTransport(cfg, adapter=adapter))
        return WebhookHandler(self.wallet, client, self.audit, Reconciler(self.wallet, client, self.audit))

    def test_no_webhook_id_503_and_no_network(self):
        stub = StubAdapter()
        res = self.handler(stub, sandbox_cfg()).handle(b'{"id":"a","event_type":"x"}', self.HDR)
        self.assertEqual((res.status, res.code), (503, "WEBHOOK_NOT_CONFIGURED"))
        self.assertEqual(stub.seen, [])

    def test_failure_status_from_paypal_is_a_rejection(self):
        stub = StubAdapter(default=(200, {"verification_status": "FAILURE"}))
        res = self.handler(stub, sandbox_cfg(webhook_id="WID")).handle(b'{"id":"a","event_type":"x"}', self.HDR)
        self.assertEqual((res.status, res.code), (400, "BAD_SIGNATURE"))

    def test_paypal_unreachable_means_not_verified(self):
        import requests
        stub = StubAdapter([requests.exceptions.ConnectTimeout("x")])
        res = self.handler(stub, sandbox_cfg(webhook_id="WID")).handle(b'{"id":"a","event_type":"x"}', self.HDR)
        self.assertEqual((res.status, res.code), (503, "CANNOT_VERIFY"))


class TestDedupAndOrdering(Base):
    def test_duplicate_id_has_no_effect_even_after_a_restart_of_the_dedup_table(self):
        rec = self.send()
        self.release_batches()
        ev = next(x for x in self.sim.due_events() if b"PAYOUTS-ITEM" in x["raw"])
        self.assertEqual(self.gateway.webhooks.handle(ev["raw"], ev["headers"]).code, "OK")
        n = len(rec["evidence"])
        self.assertEqual(self.gateway.webhooks.handle(ev["raw"], ev["headers"]).code, "DUPLICATE")
        self.assertEqual(len(rec["evidence"]), n)
        self.assertIn(json.loads(ev["raw"])["id"], self.wallet.webhook_events)

    def test_a_capture_event_before_the_return_still_verifies_the_order(self):
        self.turn("Get me a blood pressure monitor")
        rec = self.rec(self.sign())
        oid = rec["paypal"]["order_id"]
        self.sim.buyer_decide_order(oid, "approve")
        # The capture happened and its event arrives before our own call finished
        self.gateway.client.capture_order(oid, rec["digest"])
        self.deliver()
        self.assertEqual((rec["state"], rec["verified"]), ("PAID", True))

    def test_terminal_state_does_not_reopen(self):
        rec = self.send()
        self.release_batches()
        self.deliver()
        self.assertEqual(rec["state"], "SENT")
        event = {"id": "WH-LATE", "event_type": "PAYMENT.PAYOUTS-ITEM.UNCLAIMED",
                 "resource": {"payout_item_id": rec["paypal"]["item_id"], "payout_batch_id": rec["paypal"]["batch_id"]}}
        raw = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(self.gateway.webhooks.handle(raw, self.sim._sign(event)).code, "OK")
        self.assertEqual(rec["state"], "SENT")

    def test_amount_mismatch_is_an_anomaly_never_success(self):
        rec = self.send()
        event = {"id": "WH-MIS", "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
                 "resource": {"payout_batch_id": rec["paypal"]["batch_id"], "payout_item": {"amount": {"currency": "USD", "value": "5000.00"}}}}
        raw = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        self.gateway.webhooks.handle(raw, self.sim._sign(event))
        self.assertEqual((rec["state"], rec["verified"]), ("ANOMALY", False))
        self.assertTrue(any(a["kind"] == "anomaly" for a in self.wallet.user(USER)["alerts"]))

    def test_a_batch_event_doesnt_say_the_money_arrived(self):
        rec = self.send()
        event = {"id": "WH-B", "event_type": "PAYMENT.PAYOUTSBATCH.SUCCESS", "resource": {"batch_header": {"payout_batch_id": rec["paypal"]["batch_id"]}}}
        raw = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        self.gateway.webhooks.handle(raw, self.sim._sign(event))
        self.assertEqual(rec["state"], "PROCESSING")

    def test_dispute_event_raises_a_family_alert(self):
        event = {"id": "WH-D", "event_type": "CUSTOMER.DISPUTE.CREATED", "resource": {"dispute_id": "PP-1"}}
        raw = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        self.wallet.user(USER)
        self.gateway.webhooks.handle(raw, self.sim._sign(event))
        self.assertTrue(any(a["kind"] == "dispute" for a in self.wallet.user(USER)["alerts"]))

    def test_every_webhook_is_on_the_audit_chain(self):
        self.send()
        self.release_batches()
        self.deliver()
        entries = [json.loads(line) for line in self.audit.path.read_text(encoding="utf8").splitlines()]
        self.assertTrue(any(x["event"] == "paypal.webhook" and x["data"]["result"].startswith("SENT") for x in entries))
        self.assertTrue(self.audit.verify()["ok"])
