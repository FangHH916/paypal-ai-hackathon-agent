"""POST /api/paypal/webhook, after the HTTP layer. Nothing from the outside changes state until PayPal itself says the
signature is good, through POST /v1/notifications/verify-webhook-signature. Then: dedup by event id, match to a record
by its PayPal ids, check amounts against the signed card, and move the record. Terminal states don't reopen. A
success set here carries verified_by "webhook:<event id>".

Event names come from PayPal's webhook event names page. The resource shapes for the payout and invoice events are only
partly documented, so the ids are read from every place the docs show them; an event that matches nothing is logged and
acknowledged. Unverified against the real sandbox: run scripts/paypal_sandbox_check.py with a real webhook id.
"""
from __future__ import annotations

import json

from app.paypal import calls
from app.paypal.errors import PayPalConfigError, PayPalError, PayPalUnknown
from app.paypal.simulator import WEBHOOK_ID as SIM_WEBHOOK_ID
from app.wallet import payments

REQUIRED = ("paypal-transmission-id", "paypal-transmission-time", "paypal-transmission-sig", "paypal-cert-url", "paypal-auth-algo")


class WebhookResult:
    def __init__(self, status: int, code: str, detail: str = "", state: str = "", payment_id: str = ""):
        self.status, self.code, self.detail, self.state, self.payment_id = status, code, detail, state, payment_id


class WebhookHandler:
    def __init__(self, wallet, client, audit, reconciler):
        self.wallet, self.client, self.audit, self.rec = wallet, client, audit, reconciler

    @property
    def webhook_id(self) -> str:
        return SIM_WEBHOOK_ID if self.rec.simulator else self.client.cfg.webhook_id

    def handle(self, raw: bytes, headers: dict) -> WebhookResult:
        headers = {k.lower(): v for k, v in headers.items()}
        missing = [h for h in REQUIRED if not headers.get(h)]
        if missing:
            return self._reject(400, "MISSING_HEADERS", f"Missing {', '.join(missing)}")
        if not self.webhook_id:
            return self._reject(503, "WEBHOOK_NOT_CONFIGURED", "PAYPAL_WEBHOOK_ID is not set. Star keeps working by asking PayPal directly.")
        try:
            event = json.loads(raw)
            if (not isinstance(event, dict) or not isinstance(event.get("id"), str) or not event["id"]
                    or not isinstance(event.get("event_type"), str) or not event["event_type"]
                    or not isinstance(event.get("resource", {}), dict)):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return self._reject(400, "BAD_BODY", "Not a webhook event")
        try:
            verdict = self.client.verify_webhook_signature(raw_event=raw, headers=headers, webhook_id=self.webhook_id)
        except (PayPalUnknown, PayPalError, PayPalConfigError) as e:
            # Can't verify = not verified. 503 asks PayPal to deliver it again later.
            return self._reject(503, "CANNOT_VERIFY", type(e).__name__)
        if verdict.get("verification_status") != "SUCCESS":
            return self._reject(400, "BAD_SIGNATURE", "PayPal did not verify this signature", event)
        result = self.wallet.process_webhook(event["id"], event["event_type"], lambda: self._route(event))
        if self.audit:
            self.wallet.flush_audit(self.audit)
        return result if result is not None else WebhookResult(200, "DUPLICATE")

    def _reject(self, status: int, code: str, detail: str, event: dict | None = None) -> WebhookResult:
        if self.audit:
            self.audit.append("paypal.webhook_rejected", "", {"code": code, "detail": detail[:120],
                                                              "event_id": (event or {}).get("id", ""), "type": (event or {}).get("event_type", "")})
        return WebhookResult(status, code, detail)

    # ---------------------------------------------------------------- routing
    def _route(self, event: dict) -> WebhookResult:
        etype, res = event["event_type"], event.get("resource") or {}
        if etype.startswith("CUSTOMER.DISPUTE."):
            return self._dispute(event, res)
        rec = self._match(etype, res)
        if not rec:
            self._log(event, None, "unmatched")
            return WebhookResult(200, "UNMATCHED")
        vb, eid = f"webhook:{event['id']}", event["id"]
        before = rec["state"]
        with calls.scope(rec["user_id"], rec["session_id"], rec["payment_id"]):
            try:
                self._apply(rec, etype, res, vb, eid)
            except payments.BadTransition:
                self.wallet.update_payment(rec["payment_id"], event_id=eid)   # terminal state: don't reopen, just note it
        self._log(event, rec, rec["state"] if rec["state"] != before else f"{before} (no change)")
        return WebhookResult(200, "OK", state=rec["state"], payment_id=rec["payment_id"])

    def _apply(self, rec, etype, res, vb, eid) -> None:
        if etype.startswith("PAYMENT.PAYOUTS-ITEM."):
            status = {"SUCCEEDED": "SUCCESS", "UNCLAIMED": "UNCLAIMED", "FAILED": "FAILED", "RETURNED": "RETURNED", "BLOCKED": "BLOCKED",
                      "HELD": "ONHOLD", "CANCELED": "RETURNED", "REFUNDED": "REFUNDED", "DENIED": "FAILED"}.get(etype.rsplit(".", 1)[1])
            if rec["rail"] == "payout_cancel":
                if status == "RETURNED":
                    from app.paypal.reconcile import _amount_matches
                    if _amount_matches((res.get("payout_item") or {}).get("amount"), rec, "currency"):
                        self.wallet.update_payment(rec["payment_id"], "REFUNDED", verified_by=vb, source="webhook", event_type=etype, event_id=eid)
                    else:
                        self.rec._anomaly(rec, "The returned payout amount or currency doesn't match what you signed.", eid, etype)
                return
            self.rec.apply_item(rec, {**res, "transaction_status": status or res.get("transaction_status")}, vb, event_id=eid, event_type=etype)
        elif etype == "PAYMENT.PAYOUTSBATCH.DENIED":
            self.wallet.update_payment(rec["payment_id"], "FAILED", source="webhook", event_type=etype, event_id=eid, note="PayPal denied the batch")
        elif etype.startswith("PAYMENT.PAYOUTSBATCH."):
            self.wallet.update_payment(rec["payment_id"], event_id=eid)      # a batch event doesn't say the money arrived
        elif etype == "CHECKOUT.ORDER.APPROVED":
            if rec["state"] == "AWAITING_APPROVAL":
                self.wallet.update_payment(rec["payment_id"], "APPROVED", source="webhook", event_type=etype, event_id=eid, note="Approved by the buyer")
        elif etype == "PAYMENT.CAPTURE.REFUNDED":
            self.rec.apply_refund(rec, res, vb, event_id=eid, event_type=etype)
        elif etype == "PAYMENT.CAPTURE.REVERSED":
            self.wallet.update_payment(rec["payment_id"], "REVERSED", verified_by=vb, source="webhook", event_type=etype, event_id=eid, note="PayPal reversed this payment")
        elif etype.startswith("PAYMENT.CAPTURE."):
            status = {"COMPLETED": "COMPLETED", "PENDING": "PENDING", "DENIED": "DECLINED", "DECLINED": "DECLINED"}.get(etype.rsplit(".", 1)[1])
            self.rec.apply_capture(rec, {**res, "status": status}, vb, event_id=eid, event_type=etype)
        elif etype.startswith("INVOICING.INVOICE."):
            inv = res.get("invoice") if isinstance(res.get("invoice"), dict) else res
            status = {"PAID": "PAID", "CANCELLED": "CANCELLED", "REFUNDED": "REFUNDED"}.get(etype.rsplit(".", 1)[1], inv.get("status"))
            self.rec.apply_invoice(rec, {**inv, "status": status}, vb, event_id=eid, event_type=etype)

    def _match(self, etype: str, res: dict) -> dict | None:
        rows = list(self.wallet.payments.values())

        def by(key: str, val) -> dict | None:
            return next((r for r in rows if val and r["paypal"].get(key) == val), None) if val else None
        if etype.startswith("PAYMENT.PAYOUTS-ITEM."):
            item, batch = res.get("payout_item_id"), res.get("payout_batch_id")
            cancel = next((r for r in rows if r["rail"] == "payout_cancel" and item and r["paypal"].get("item_id") == item and r["state"] not in payments.TERMINAL), None)
            return cancel or next((r for r in rows if r["rail"] == "payouts" and (r["paypal"].get("item_id") == item or r["paypal"].get("batch_id") == batch)), None)
        if etype.startswith("PAYMENT.PAYOUTSBATCH."):
            return by("batch_id", (res.get("batch_header") or {}).get("payout_batch_id"))
        if etype == "CHECKOUT.ORDER.APPROVED":
            return by("order_id", res.get("id"))
        if etype == "PAYMENT.CAPTURE.REFUNDED":
            capture = _up_capture(res)
            refund = by("refund_id", res.get("id"))
            if refund:
                return refund
            return next((r for r in rows if r["rail"] == "refund" and capture and r["card"].get("capture_id") == capture and r["state"] not in payments.TERMINAL), None)
        if etype.startswith("PAYMENT.CAPTURE."):
            order = ((res.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
            return by("capture_id", res.get("id")) or by("order_id", order) or next(
                (r for r in rows if r["rail"] == "orders" and res.get("custom_id") and r["digest"].startswith(res["custom_id"])), None)
        if etype.startswith("INVOICING.INVOICE."):
            inv = res.get("invoice") if isinstance(res.get("invoice"), dict) else res
            return by("invoice_id", inv.get("id"))
        return None

    def _dispute(self, event: dict, res: dict) -> WebhookResult:
        """Disputes are read only here. An event raises a family alert (for the household's own demo account)."""
        word = event["event_type"].rsplit(".", 1)[1].lower()
        for user_id in list(self.wallet.users()):
            self.wallet.add_alert(user_id, "warn", "dispute", f"A PayPal case was {word}: {res.get('dispute_id', '')}")
        self._log(event, None, f"dispute {word}")
        return WebhookResult(200, "OK")

    def _log(self, event: dict, rec: dict | None, result: str) -> None:
        if self.audit:
            self.audit.append("paypal.webhook", rec["session_id"] if rec else "", {
                "event_id": event["id"], "type": event["event_type"], "payment_id": rec["payment_id"] if rec else "", "result": result})


def _up_capture(res: dict) -> str:
    for link in res.get("links") or []:
        href = str(link.get("href") or "")
        if link.get("rel") == "up" and "/captures/" in href:
            return href.rsplit("/", 1)[-1]
    return ""
