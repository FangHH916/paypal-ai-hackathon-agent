"""PayPalClient: every call that moves money or acts in the customer's name.

Only the execution gateway, reconcile.py, webhooks.py and the scripts may import this module (tests/test_architecture.py).
All request shapes follow PayPal's official OpenAPI files (github.com/paypal/paypal-rest-api-specifications), and
tests/test_paypal_spec.py checks every request against a subset of them. Nothing in the test suite has reached the real
sandbox: scripts/paypal_sandbox_check.py does that once someone supplies keys.
"""
from __future__ import annotations

import json

from app.paypal import ids
from app.paypal.reader import PayPalReader


def value(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def money(cents: int, currency: str) -> dict:
    return {"currency_code": currency, "value": value(cents)}


def payer_link(resp: dict) -> str:
    """Where the buyer approves. Spec: rel "payer-action" when payment_source is sent, "approve" otherwise."""
    for rel in ("payer-action", "approve"):
        href = next((l.get("href") for l in resp.get("links") or [] if l.get("rel") == rel), "")
        if href:
            return href
    return ""


class PayPalClient(PayPalReader):
    # ---------------------------------------------------------------- Payouts
    def create_payout(self, digest: str, *, amount_cents: int, currency: str, receiver: str, note: str,
                      subject: str = "", message: str = "") -> dict:
        batch = ids.sender_batch_id(digest)
        body = {"sender_batch_header": {"sender_batch_id": batch, "email_subject": subject or "You have a payment",
                                        "email_message": message or note},
                "items": [{"recipient_type": "EMAIL", "amount": {"currency": currency, "value": value(amount_cents)},
                           "receiver": receiver, "note": note, "sender_item_id": ids.sender_item_id(digest)}]}
        return self.t.request("payout_create", "POST", "/v1/payments/payouts", body=body, write=True,
                              headers={"PayPal-Request-Id": batch})

    def cancel_payout_item(self, item_id: str) -> dict:
        return self.t.request("payout_item_cancel", "POST", "/v1/payments/payouts-item/{id}/cancel",
                              path=f"/v1/payments/payouts-item/{item_id}/cancel", write=True)

    # ---------------------------------------------------------------- Orders
    def create_order(self, digest: str, *, reference_id: str, items: list[dict], currency: str, description: str,
                     return_url: str, cancel_url: str, brand_name: str = "Star") -> dict:
        total = sum(i["unit_cents"] * i["quantity"] for i in items)
        unit = {"reference_id": reference_id, "custom_id": ids.custom_id(digest),
                "invoice_id": ids.order_invoice_id(digest), "description": description[:127],
                "amount": {**money(total, currency), "breakdown": {"item_total": money(total, currency)}},
                "items": [{"name": i["name"][:127], "quantity": str(i["quantity"]),
                           "unit_amount": money(i["unit_cents"], currency),
                           "category": i.get("paypal_category", "PHYSICAL_GOODS")} for i in items]}
        body = {"intent": "CAPTURE", "purchase_units": [unit],
                "payment_source": {"paypal": {"experience_context": {
                    "brand_name": brand_name, "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING",
                    "return_url": return_url, "cancel_url": cancel_url}}}}
        return self.t.request("order_create", "POST", "/v2/checkout/orders", body=body, write=True,
                              headers={"PayPal-Request-Id": ids.order_key(digest), "Prefer": "return=representation"})

    def capture_order(self, order_id: str, digest: str) -> dict:
        return self.t.request("order_capture", "POST", "/v2/checkout/orders/{id}/capture",
                              path=f"/v2/checkout/orders/{order_id}/capture", body={}, write=True,
                              headers={"PayPal-Request-Id": ids.capture_key(digest), "Prefer": "return=representation"})

    # ---------------------------------------------------------------- Refund
    def refund_capture(self, capture_id: str, digest: str, *, amount_cents: int | None = None, currency: str = "",
                       note: str = "") -> dict:
        """No amount in the body means a full refund (spec). Send one for a partial refund."""
        body: dict = {}
        if amount_cents is not None:
            body["amount"] = money(amount_cents, currency)
        if note:
            body["note_to_payer"] = note[:255]
        return self.t.request("capture_refund", "POST", "/v2/payments/captures/{id}/refund",
                              path=f"/v2/payments/captures/{capture_id}/refund", body=body, write=True,
                              headers={"PayPal-Request-Id": ids.refund_key(digest), "Prefer": "return=representation"})

    # ---------------------------------------------------------------- Invoicing
    # The spec lists no idempotency header on these two, so detail.invoice_number plays that role: reconcile searches
    # by it before any retry.
    def create_invoice(self, *, number: str, currency: str, recipient_email: str, item_name: str, amount_cents: int,
                       note: str = "", reference: str = "", invoicer_name: str = "", invoicer_email: str = "",
                       terms: str = "", due_date: str = "") -> dict:
        detail = {"invoice_number": number, "currency_code": currency}
        for field, val in (("note", note[:4000]), ("reference", reference[:120]), ("terms_and_conditions", terms[:4000])):
            if val:
                detail[field] = val
        if due_date:
            detail["payment_term"] = {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": due_date}
        body = {"detail": detail, "primary_recipients": [{"billing_info": {"email_address": recipient_email}}],
                "items": [{"name": item_name[:200], "quantity": "1", "unit_amount": money(amount_cents, currency)}]}
        invoicer = {k: v for k, v in (("business_name", invoicer_name), ("email_address", invoicer_email)) if v}
        if invoicer:
            body["invoicer"] = invoicer
        return self.t.request("invoice_create", "POST", "/v2/invoicing/invoices", body=body, write=True,
                              headers={"Prefer": "return=representation"})

    def send_invoice(self, invoice_id: str, *, note: str = "") -> dict:
        body = {"send_to_recipient": True, "send_to_invoicer": False}
        if note:
            body["note"] = note[:4000]
        return self.t.request("invoice_send", "POST", "/v2/invoicing/invoices/{id}/send",
                              path=f"/v2/invoicing/invoices/{invoice_id}/send", body=body, write=True)

    # ---------------------------------------------------------------- Webhooks
    def verify_webhook_signature(self, *, raw_event: bytes, headers: dict, webhook_id: str | None = None) -> dict:
        """Splices the raw event bytes into the request instead of re-serialising them. Re-serialising is widely
        reported to break verification. We have not confirmed that claim, but sending the raw bytes is safe either way."""
        fields = {"auth_algo": headers["paypal-auth-algo"], "cert_url": headers["paypal-cert-url"],
                  "transmission_id": headers["paypal-transmission-id"],
                  "transmission_sig": headers["paypal-transmission-sig"],
                  "transmission_time": headers["paypal-transmission-time"],
                  "webhook_id": webhook_id or self.cfg.webhook_id}
        head = json.dumps(fields, separators=(",", ":"))[:-1].encode()
        raw = head + b',"webhook_event":' + raw_event + b"}"
        return self.t.request("webhook_verify", "POST", "/v1/notifications/verify-webhook-signature", raw_body=raw)
