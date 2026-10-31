"""Payouts, Orders, Refunds, Invoices: the full flow through agent, kernel, signed card, gateway and simulator."""
import json
from unittest.mock import patch

from app.config import settings
from scripts.verify_receipt import check
from tests.paypal_helpers import USER, World


class Limitless(World):
    def setUp(self):
        super().setUp()
        p = patch.object(settings, "velocity_max", 50)
        p.start()
        self.addCleanup(p.stop)


class TestPayouts(Limitless):
    def test_send_to_family_reaches_verified_sent(self):
        self.turn("Send 50 to my daughter")
        r = self.sign()
        rec = self.rec(r)
        self.assertEqual((r["status"], rec["state"], rec["verified"]), ("SUCCESS", "SENT", True))
        self.assertTrue(rec["verified_by"].startswith("get:/v1/payments/payouts/"))
        body = json.loads(self.writes("/v1/payments/payouts")[0]["body"])
        self.assertEqual(body["items"][0]["receiver"], "anna.lee@family.example")
        self.assertEqual(body["sender_batch_header"]["sender_batch_id"], "star-" + rec["digest"][:40])
        self.assertTrue(all(ok for _, ok in check(r["verifiable"])), check(r["verifiable"]))

    def test_sandbox_mapping_overrides_the_email_and_is_signed(self):
        self.gateway.paypal.cfg.receivers["p_daughter"] = "sandbox-anna@personal.example"
        self.turn("Send 50 to my daughter")
        d = self.s.current_draft
        self.assertEqual(d.card_fields()["payee_handle"], "sandbox-anna@personal.example")
        self.assertEqual(self.gateway.receiver_for({"payee_id": "x", "paypal_email": "a@b.example"}), "a@b.example")

    def test_nothing_is_sent_before_the_signature(self):
        self.turn("Send 50 to my daughter")
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.wallet.allowance_left(USER), 150000)

    def test_a_stranger_is_unclaimed_and_can_be_stopped(self):
        self.wallet.add_payee(USER, "Tom Reed", "tom.reed@mail.example", "friend")
        self.turn("Send 20 to Tom Reed")
        rec = self.rec(self.sign())
        self.assertEqual(rec["state"], "UNCLAIMED")
        left = self.wallet.allowance_left(USER)
        self.turn("Stop that payment")
        d = self.s.current_draft
        self.assertEqual((d.kind, d.rail, d.amount_cents), ("refund", "payout_cancel", 2000))
        stop = self.rec(self.sign())
        self.assertEqual((stop["state"], stop["verified"]), ("REFUNDED", True))
        self.assertEqual(rec["state"], "RETURNED")
        self.assertEqual(self.wallet.allowance_left(USER), left + 2000)

    def test_a_definite_refusal_gives_the_allowance_back(self):
        from app.kernel.executor import KernelError
        self.turn("Send 50 to my daughter")
        with patch.object(self.gateway.client, "create_payout", side_effect=__import__("app.paypal.errors", fromlist=["PayPalError"]).PayPalError(422, "RECEIVER_UNREGISTERED")):
            with self.assertRaises(KernelError) as e:
                self.sign()
        self.assertEqual(e.exception.code, "GW_PAYPAL_REJECTED")
        rec = self.wallet.payments_for(USER)[0]
        self.assertEqual(rec["state"], "FAILED")
        self.assertEqual(self.wallet.allowance_left(USER), 150000)

    def test_batch_success_alone_is_not_money_arrived(self):
        self.turn("Send 50 to my daughter")
        with patch.object(self.rec_mod, "_refresh_payout"):
            rec = self.rec(self.sign())
        self.assertEqual((rec["state"], rec["verified"]), ("PROCESSING", False))


class TestOrders(Limitless):
    def test_find_offer_sign_approve_capture_verify(self):
        r = self.turn("Get me a blood pressure monitor")
        self.assertEqual(r.ui["card"]["merchant"], "Corner Pharmacy")
        self.assertEqual(self.writes(), [])
        receipt = self.sign()
        rec = self.rec(receipt)
        self.assertEqual((rec["state"], receipt["status"]), ("AWAITING_APPROVAL", "PENDING"))
        self.assertEqual(receipt["next_step"]["type"], "redirect")
        self.assertTrue(receipt["next_step"]["url"].startswith("/simulator/checkout?token="))
        self.sim.buyer_decide_order(rec["paypal"]["order_id"], "approve")
        self.assertNotEqual(rec["state"], "PAID")                   # the approval page alone proves nothing
        self.rec_mod.order_return(rec)
        self.assertEqual((rec["state"], rec["verified"]), ("PAID", True))
        self.assertTrue(rec["verified_by"].startswith("get:/v2/checkout/orders/"))
        self.assertTrue(rec["paypal"]["capture_id"])
        self.assertEqual(len(self.writes("/capture")), 1)

    def test_return_url_points_back_with_the_payment(self):
        self.turn("Get me a blood pressure monitor")
        rec = self.rec(self.sign(return_base="https://star.example"))
        ctx = self.sim.st["orders"][rec["paypal"]["order_id"]]["_ctx"]
        self.assertEqual(ctx["return_url"], f"https://star.example/?pp_return=1&payment={rec['payment_id']}")

    def test_return_is_idempotent(self):
        rec = self.buy()
        self.rec_mod.order_return(rec)
        self.rec_mod.order_return(rec)
        self.assertEqual(len(self.sim.st["captures"]), 1)

    def test_return_without_approval_captures_nothing(self):
        self.turn("Get me a blood pressure monitor")
        rec = self.rec(self.sign())
        self.rec_mod.order_return(rec)
        self.assertEqual(rec["state"], "AWAITING_APPROVAL")
        self.assertEqual(self.writes("/capture"), [])

    def test_buyer_cancel_releases_the_allowance(self):
        self.turn("Get me a blood pressure monitor")
        rec = self.rec(self.sign())
        self.assertEqual(self.wallet.allowance_left(USER), 150000 - 3900)
        self.rec_mod.order_cancelled(rec)
        self.assertEqual(rec["state"], "BUYER_CANCELLED")
        self.assertEqual(self.wallet.allowance_left(USER), 150000)

    def test_declined_card_can_try_again_on_the_same_order(self):
        self.turn("Get me a blood pressure monitor")
        rec = self.rec(self.sign())
        self.sim.buyer_decide_order(rec["paypal"]["order_id"], "approve")
        self.sim.arm(USER, "decline_instrument", op="order_capture")
        self.rec_mod.order_return(rec)
        self.assertEqual(rec["state"], "AWAITING_APPROVAL")
        self.assertIn("declined", rec["evidence"][-1]["note"])
        self.rec_mod.order_return(rec)
        self.assertEqual(rec["state"], "PAID")

    def test_capture_amount_mismatch_is_an_anomaly(self):
        self.turn("Get me a blood pressure monitor")
        rec = self.rec(self.sign())
        self.sim.buyer_decide_order(rec["paypal"]["order_id"], "approve")
        self.sim.arm(USER, "amount_mismatch", op="order_capture")
        self.rec_mod.order_return(rec)
        self.assertEqual((rec["state"], rec["verified"]), ("ANOMALY", False))
        self.assertTrue(any(a["kind"] == "anomaly" for a in self.wallet.user(USER)["alerts"]))

    def test_pending_capture_is_not_paid(self):
        self.turn("Get me a blood pressure monitor")
        rec = self.rec(self.sign())
        self.sim.buyer_decide_order(rec["paypal"]["order_id"], "approve")
        self.sim.arm(USER, "capture_pending", op="order_capture")
        self.rec_mod.order_return(rec)
        self.assertEqual(rec["state"], "CAPTURE_PENDING")
        self.assertFalse(rec["verified"])

    def test_unsafe_redirect_host_is_not_offered(self):
        self.assertEqual(self.gateway._safe_redirect("https://evil.example/simulator/checkout"), "")
        self.assertEqual(self.gateway._safe_redirect("https://paypal-simulator.invalid/elsewhere"), "")


class TestRefunds(Limitless):
    def test_voice_refund_verified_and_allowance_back(self):
        rec = self.buy()
        self.assertEqual(self.wallet.allowance_left(USER), 150000 - 3900)
        self.turn("Stop! I want my money back")
        d = self.s.current_draft
        self.assertEqual((d.kind, d.rail, d.amount_cents, d.capture_id), ("refund", "refund", 3900, rec["paypal"]["capture_id"]))
        refund = self.rec(self.sign())
        self.assertEqual((refund["state"], refund["verified"]), ("REFUNDED", True))
        self.assertEqual(rec["state"], "REFUNDED")
        self.assertEqual(self.wallet.allowance_left(USER), 150000)

    def test_a_refund_needs_a_signature(self):
        self.buy()
        self.turn("I want my money back")
        self.assertEqual(self.writes("/refund"), [])

    def test_nothing_to_refund_says_so(self):
        r = self.turn("I want my money back")
        self.assertIn("don't see a payment", r.reply)

    def test_unverified_purchase_cannot_be_refunded(self):
        self.turn("Get me a blood pressure monitor")
        rec = self.rec(self.sign())
        self.s.messages.append({"role": "user", "content": "I want my money back for the blood pressure monitor"})
        res = self.tools.call(self.s, "draft_refund", {"payment_id": rec["payment_id"]})
        self.assertTrue(res["ok"], res)
        self.assertIn("R95_REFUND_INVALID", self.s.current_draft.risk.rule_hits)
        self.assertEqual(self.s.current_draft.risk.level.value, "BLOCK")

    def test_a_refund_of_someone_elses_payment_has_no_draft(self):
        rec = self.buy()
        from app.kernel.state import Session
        other = Session(session_id="o", user_id="u_lee~other")
        other.messages.append({"role": "user", "content": "I want my money back"})
        self.assertFalse(self.tools.call(other, "draft_refund", {"payment_id": rec["payment_id"]})["ok"])


class TestInvoices(Limitless):
    def test_bill_list_comes_through_paypal_search(self):
        r = self.turn("What bills do I have?")
        self.assertIn("Riverside Power", r.reply)
        self.assertTrue(any(q["path"] == "/v2/invoicing/search-invoices" for q in self.sim.requests_seen))
        self.assertTrue(self.wallet.invoice(USER, "inv_power_oct")["paypal_id"].startswith("INV2-"))

    def test_paying_a_bill_sends_her_to_the_payer_page_and_only_paid_says_paid(self):
        self.turn("Pay the power bill")
        receipt = self.sign()
        rec = self.rec(receipt)
        self.assertEqual(rec["state"], "AWAITING_PAYMENT")
        self.assertTrue(receipt["next_step"]["url"].startswith("/simulator/invoice?id="))
        self.assertEqual(self.writes(), [])                         # REST has no payer-side pay: Star makes no write here
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual(rec["state"], "AWAITING_PAYMENT")
        self.sim.payer_pay_invoice(rec["paypal"]["invoice_id"])
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual((rec["state"], rec["verified"]), ("PAID", True))
        self.assertEqual(self.wallet.invoice(USER, "inv_power_oct")["status"], "PAID")

    def test_marked_as_paid_is_not_paid(self):
        self.turn("Pay the power bill")
        rec = self.rec(self.sign())
        self.sim.st["invoices"][rec["paypal"]["invoice_id"]]["status"] = "MARKED_AS_PAID"
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual(rec["state"], "AWAITING_PAYMENT")

    def test_the_invoice_amount_changing_after_signing_is_an_anomaly(self):
        self.turn("Pay the water bill")
        d = self.s.current_draft
        self.gateway.invoices.sync(USER, force=True)
        pid = self.wallet.invoice(USER, "inv_water_oct")["paypal_id"]
        self.sim.st["invoices"][pid]["due_amount"]["value"] = "420.00"
        rec = self.rec(self.sign(d))
        self.assertEqual(rec["state"], "ANOMALY")

    def test_ask_my_son_to_pay_me_back(self):
        r = self.turn("Ask my son to pay me back 40 dollars")
        d = self.s.current_draft
        self.assertEqual((d.kind, d.rail, d.payee_id, d.amount_cents), ("money_request", "money_request", "p_son", 4000))
        self.assertIn("Ask David Lee", r.ui["card"]["title"] + r.ui["card"]["readback"].replace("You want to ask", "Ask"))
        self.assertEqual(self.writes(), [])
        rec = self.rec(self.sign())
        self.assertEqual(rec["state"], "SENT_TO_PAYER")
        self.assertEqual(rec["paypal"]["invoice_number"], "STAR-" + rec["digest"][:16])
        self.assertEqual(self.wallet.allowance_left(USER), 150000)   # asking for money spends nothing
        self.sim.payer_pay_invoice(rec["paypal"]["invoice_id"])
        self.rec_mod.refresh(rec, force=True)
        self.assertEqual(rec["state"], "PAID")


class TestTheInvoiceThatTalkedBack(Limitless):
    def setUp(self):
        super().setUp()
        self.gateway.invoices.arrive_fake(USER)

    def test_explained_as_a_warning_with_the_instruction_removed(self):
        r = self.turn("What's this bill?")
        self.assertIn("looks wrong", r.reply)
        self.assertIn("555-0142", self.wallet.invoice(USER, "inv_securetech")["note"])
        self.assertNotIn("approve and pay", r.reply.lower())
        result = self.tools.call(self.s, "explain_invoice", {"invoice_id": "inv_securetech"})["data"]
        self.assertTrue(result["suspicious_removed"])
        self.assertNotIn("approve and pay", result["note"].lower())
        self.assertIn("[suspicious instruction removed]", result["note"])
        self.assertFalse(result["can_pay"])

    def test_family_gets_an_alert_and_paypal_hears_nothing(self):
        self.turn("What's this bill?")
        self.assertTrue(any("SecureTech" in a["text"] for a in self.wallet.user(USER)["alerts"]))
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.gateway.calls.money_moving_count(USER), 0)

    def test_paying_it_is_blocked_by_code(self):
        r = self.turn("Pay the SecureTech bill")
        d = self.s.current_draft
        self.assertEqual(r.ui["type"], "blocked")
        for code in ("R84_INSTRUCTION_IN_DATA", "R80_INVOICE_PHONE", "R81_INVOICE_URGENT", "R82_BILLER_UNKNOWN"):
            self.assertIn(code, d.risk.rule_hits)
        self.assertEqual(self.writes(), [])

    def test_a_model_that_obeys_the_note_is_still_stopped(self):
        self.s.messages.append({"role": "user", "content": "What's this bill?"})
        res = self.tools.call(self.s, "draft_bill_payment", {"invoice_id": "inv_securetech"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"]["risk_level"], "BLOCK")


class TestStopsReachTheActivityGrid(Limitless):
    def test_blocked_and_frozen_are_rows(self):
        self.turn("Get me a blood pressure monitor from MedDeals Direct")
        self.s.settle()
        self.tools.call(self.s, "list_payees", {})
        self.s.messages.append({"role": "user", "content": "Send 50 to my daughter"})
        self.tools.call(self.s, "draft_transfer", {"payee_id": "p_daughter", "amount": 500})
        rows = self.wallet.stops(USER)
        self.assertEqual([r["status"] for r in rows], ["FROZEN", "STOPPED"])
        self.assertEqual(rows[1]["stopped_by"], "Hidden instruction in the text")
