"""From a signed card to verified state. Every write to PayPal, and every way of finding out what happened to it.

The rules here are the failure semantics of the whole product:
  * A record exists before its HTTP call (the gateway saves it). Its keys come from the signed digest.
  * An unknown outcome (timeout, reset, 5xx) is never retried with a new key and never reported as a failure. It is
    resolved by asking PayPal, using the matrix below, and Star says "please don't pay again" until then.
  * "Sent", "Paid" and "Refunded" come only from a GET here or a signature-verified webhook (I5), and an amount or
    identity that disagrees with the signed card is an ANOMALY: never a success.

Unknown at        Query                                                  Then
payout create     identical re-POST (same body, key): the documented      batch found: GET it. Otherwise stays UNKNOWN
                  duplicate rule makes it a lookup
order create      identical POST with the same PayPal-Request-Id          order found: continue. After 6 h: ABANDONED
capture           GET order, look for captures                            found: verify. None and APPROVED: same request id
refund            same PayPal-Request-Id                                  found: GET and verify
payout cancel     GET item                                                RETURNED: verified. UNCLAIMED: cancel again
invoice create    search by invoice number                                found: continue. Not found: create again
"""
from __future__ import annotations

import time
import re
from decimal import Decimal

from app.config import settings
from app.paypal import calls, ids
from app.paypal.client import payer_link
from app.paypal.errors import PayPalConfigError, PayPalError, PayPalUnknown
from app.wallet import payments

ORDER_KEY_HOURS = 6                # PayPal-Request-Id is stored for 6 hours on Orders (docs)
REFRESH_EVERY = 2.0
TERMINAL_INVOICE = {"PAID": "PAID", "CANCELLED": "INVOICE_CANCELLED", "AUTO_CANCELLED": "INVOICE_CANCELLED"}


def _cents(value) -> int | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,13}(?:\.[0-9]{1,2})?", value):
        return None
    return int(Decimal(value) * 100)


def _amount_matches(amount, rec, currency_key="currency_code") -> bool:
    return (isinstance(amount, dict) and _cents(amount.get("value")) == rec["amount_cents"]
            and amount.get(currency_key) == rec["currency"])


def batch_id_from_links(err: PayPalError) -> str:
    """The original batch out of a duplicate-id rejection. The spec promises a HATEOAS link but not the exact JSON, so
    this reads links[] generically. UNVERIFIED against the real sandbox: scripts/paypal_sandbox_check.py prints it."""
    for link in err.links:
        href = str(link.get("href") or "") if isinstance(link, dict) else ""
        marker = "/v1/payments/payouts/"
        if marker in href:
            return href.split(marker, 1)[1].split("?")[0].split("/")[0]
    return ""


class Reconciler:
    def __init__(self, wallet, client, audit=None, simulator=None):
        self.wallet, self.client, self.audit, self.simulator = wallet, client, audit, simulator
        self.webhooks = None                       # set by the gateway; needed to deliver simulated events

    # ------------------------------------------------------------------ first attempt (the gateway calls this)
    def submit(self, rec: dict) -> None:
        {"payouts": self._submit_payout, "orders": self._submit_order, "refund": self._submit_refund,
         "payout_cancel": self._submit_cancel, "invoice": self._submit_bill, "money_request": self._submit_request}[rec["rail"]](rec)

    def mark_unknown(self, rec: dict, reason: str) -> None:
        if rec["state"] != "UNKNOWN":
            self.wallet.update_payment(rec["payment_id"], "UNKNOWN", source="client", note=reason[:120])
        self._flush()

    def _submit_payout(self, rec: dict) -> None:
        rec["paypal"]["stage"] = "payout_create"
        r = self.client.create_payout(rec["digest"], amount_cents=rec["amount_cents"], currency=rec["currency"],
                                      receiver=rec["counterparty_handle"], note="From Mrs. Lee, sent with Star",
                                      subject="You have a payment from your family")
        batch = (r.get("batch_header") or {}).get("payout_batch_id", "")
        if not batch:
            raise PayPalUnknown("Payout accepted but no batch id in the reply")
        self.wallet.update_payment(rec["payment_id"], "PROCESSING", paypal={"batch_id": batch}, source="POST /v1/payments/payouts")
        self._flush()
        self._refresh_payout(self.wallet.payment(rec["payment_id"]))

    def _submit_order(self, rec: dict) -> None:
        card, base = rec["card"], rec["paypal"]["return_base"]
        rec["paypal"]["stage"] = "order_create"
        qs = f"payment={rec['payment_id']}"
        items = [{**i, "paypal_category": _category(i)} for i in card["items"]]
        r = self.client.create_order(rec["digest"], reference_id=card["draft_id"], items=items, currency=rec["currency"],
                                     description=f"{items[0]['name']} from {card['merchant_name']}",
                                     return_url=f"{base}/?pp_return=1&{qs}", cancel_url=f"{base}/?pp_cancel=1&{qs}")
        self._after_order_create(rec, r)

    def _after_order_create(self, rec: dict, r: dict) -> None:
        link = payer_link(r)
        if not r.get("id") or not link:
            raise PayPalUnknown("Order accepted but no id or approval link in the reply")
        self.wallet.update_payment(rec["payment_id"], "AWAITING_APPROVAL", paypal={"order_id": r["id"], "approve_url": link},
                                   source="POST /v2/checkout/orders")
        self._flush()

    def _submit_refund(self, rec: dict) -> None:
        rec["paypal"]["stage"] = "refund"
        parent = self.wallet.payment(rec["parent_payment_id"])
        partial = rec["amount_cents"] < parent["amount_cents"] - parent.get("refunded_cents", 0)
        r = self.client.refund_capture(rec["card"]["capture_id"], rec["digest"], currency=rec["currency"],
                                       amount_cents=rec["amount_cents"] if partial or parent.get("refunded_cents") else None,
                                       note="Refund requested by the customer with Star")
        if not r.get("id"):
            raise PayPalUnknown("Refund accepted but no id in the reply")
        self.wallet.update_payment(rec["payment_id"], "REFUND_PENDING", paypal={"refund_id": r["id"]}, source="POST captures/{id}/refund")
        self._flush()
        self._refresh_refund(self.wallet.payment(rec["payment_id"]))

    def _submit_cancel(self, rec: dict) -> None:
        rec["paypal"]["stage"] = "payout_cancel"
        parent = self.wallet.payment(rec["parent_payment_id"])
        self.client.cancel_payout_item(parent["paypal"]["item_id"])
        self.wallet.update_payment(rec["payment_id"], "REFUND_PENDING", paypal={"item_id": parent["paypal"]["item_id"]},
                                   source="POST payouts-item/{id}/cancel")
        self._flush()
        self._refresh_cancel(self.wallet.payment(rec["payment_id"]))

    def _submit_bill(self, rec: dict) -> None:
        """Paying a bill has no write call: REST has no payer-side pay endpoint. Star sends her to PayPal's own view of
        the invoice. POST /v2/invoicing/invoices/{id}/payments records a payment from the issuer's side and is never used."""
        rec["paypal"]["stage"] = "invoice_read"
        inv = self.wallet.invoice(rec["user_id"], rec["card"]["invoice_id"]) or {}
        pid = inv.get("paypal_id", "")
        if not pid:
            raise PayPalError(404, "RESOURCE_NOT_FOUND", "This invoice is not at PayPal.")
        r = self.client.get_invoice(pid)
        url = ((r.get("detail") or {}).get("metadata") or {}).get("recipient_view_url", "")
        if r.get("status") in ("PAID", "MARKED_AS_PAID", "PAID_EXTERNAL", "CANCELLED", "AUTO_CANCELLED") or not url:
            raise PayPalError(422, "UNPROCESSABLE_ENTITY", "This invoice can't be paid any more.")
        if not _amount_matches(r.get("due_amount") or r.get("amount"), rec):
            self.wallet.update_payment(rec["payment_id"], "ANOMALY", source="GET invoice", note="The amount changed after you signed.")
            self._flush()
            return
        self.wallet.update_payment(rec["payment_id"], "AWAITING_PAYMENT", paypal={"invoice_id": pid, "approve_url": url},
                                   source="GET /v2/invoicing/invoices/{id}")
        self._flush()

    def _submit_request(self, rec: dict) -> None:
        rec["paypal"]["stage"] = "invoice_create"
        number = ids.invoice_number(rec["digest"])
        r = self.client.create_invoice(number=number, currency=rec["currency"], recipient_email=rec["counterparty_handle"],
                                       item_name=rec["card"].get("you_said", "")[:80] or "Paying back Mrs. Lee",
                                       amount_cents=rec["amount_cents"], note="Sent by Mrs. Lee with Star.")
        self._continue_request(rec, r.get("id") or _id_from_href(r.get("href", "")))

    def _continue_request(self, rec: dict, invoice_id: str) -> None:
        if not invoice_id:
            raise PayPalUnknown("Invoice accepted but no id in the reply")
        self.wallet.update_payment(rec["payment_id"], paypal={"invoice_id": invoice_id, "invoice_number": ids.invoice_number(rec["digest"])})
        rec["paypal"]["stage"] = "invoice_send"
        self.client.send_invoice(invoice_id)
        self._verify_request(self.wallet.payment(rec["payment_id"]))

    # ------------------------------------------------------------------ refresh: what does PayPal say now?
    def refresh(self, rec: dict, force: bool = False) -> dict:
        """Bring one record up to date with an authenticated GET. Never raises: PayPal being down changes nothing."""
        now = time.time()
        if rec["state"] in payments.TERMINAL and rec["state"] != "UNCLAIMED":
            return rec
        if not force and rec.get("next_reconcile_at", 0) > now:
            return rec
        rec["next_reconcile_at"] = now + REFRESH_EVERY
        rec["reconcile_attempts"] = rec.get("reconcile_attempts", 0) + 1
        with calls.scope(rec["user_id"], rec["session_id"], rec["payment_id"]):
            try:
                self._dispatch_refresh(rec)
            except (PayPalUnknown, PayPalError, PayPalConfigError):
                pass
            except payments.BadTransition:
                pass
        self._alert_if_stuck(rec)
        self._flush()
        return rec

    def refresh_user(self, user_id: str, force: bool = False) -> None:
        self.pump()
        for rec in self.wallet.payments_for(user_id):
            if rec["state"] not in payments.TERMINAL:
                self.refresh(rec, force=force)

    def pump(self) -> None:
        """Simulator only: hand any due signed events to the same handler a real webhook reaches."""
        if self.simulator and self.webhooks:
            for ev in self.simulator.due_events():
                self.webhooks.handle(ev["raw"], ev["headers"])

    def _dispatch_refresh(self, rec: dict) -> None:
        state = rec["state"]
        if state == "UNKNOWN":
            self._resolve_unknown(rec)
        elif rec["rail"] == "payouts":
            self._refresh_payout(rec)
        elif rec["rail"] == "orders":
            self._refresh_order(rec)
        elif rec["rail"] == "refund":
            self._refresh_refund(rec)
        elif rec["rail"] == "payout_cancel":
            self._refresh_cancel(rec)
        elif rec["rail"] == "invoice":
            self._refresh_invoice(rec)
        elif rec["rail"] == "money_request":
            self._verify_request(rec)

    # ---- Payouts. A batch SUCCESS doesn't mean the money arrived (spec): the status comes from the item.
    def _refresh_payout(self, rec: dict) -> None:
        batch_id = rec["paypal"]["batch_id"]
        if not batch_id:
            return
        path = f"get:/v1/payments/payouts/{batch_id}"
        b = self.client.get_payout_batch(batch_id)
        header = b.get("batch_header") or {}
        items = b.get("items") or []
        if not items:
            if header.get("batch_status") == "DENIED":
                self.wallet.update_payment(rec["payment_id"], "FAILED", source="GET batch", note="PayPal denied the batch")
            return
        item = items[0]
        self.apply_item(rec, item, path)

    def apply_item(self, rec: dict, item: dict, verified_by: str, *, event_id: str = "", event_type: str = "") -> None:
        pay_item = item.get("payout_item") or {}
        amount = _cents((pay_item.get("amount") or {}).get("value"))
        if pay_item.get("amount") and not _amount_matches(pay_item["amount"], rec, "currency"):
            self._anomaly(rec, "The payout amount doesn't match what you signed.", event_id, event_type)
            return
        to = {"SUCCESS": "SENT", "UNCLAIMED": "UNCLAIMED", "PENDING": "PROCESSING", "ONHOLD": "HELD", "FAILED": "FAILED",
              "BLOCKED": "FAILED", "RETURNED": "RETURNED", "REFUNDED": "RETURNED", "REVERSED": "REVERSED"}.get(item.get("transaction_status"))
        ids_ = {"item_id": item.get("payout_item_id", "")}
        if to and to != rec["state"] and payments.can_move(rec["state"], to) and to in ("SENT", "UNCLAIMED", "RETURNED", "REVERSED") \
                and not _amount_matches(pay_item.get("amount"), rec, "currency"):
            self._anomaly(rec, "The payout amount or currency is missing or doesn't match what you signed.", event_id, event_type)
            return
        if not to or (to == rec["state"]) or not payments.can_move(rec["state"], to):
            self.wallet.update_payment(rec["payment_id"], paypal=ids_, event_id=event_id)
            return
        evidence = verified_by if to in ("SENT", "RETURNED", "REVERSED", "UNCLAIMED", "FAILED") else ""
        self.wallet.update_payment(rec["payment_id"], to, paypal=ids_, verified_by=evidence if to != "FAILED" else "",
                                   source=verified_by.split(":", 1)[0], event_type=event_type, event_id=event_id,
                                   note=f"PayPal item status {item.get('transaction_status')}")

    # ---- Orders. Capture never happens on a GET: only order_return, a POST from the app, may capture.
    def _refresh_order(self, rec: dict) -> None:
        order_id = rec["paypal"]["order_id"]
        if not order_id:
            return
        o = self.client.get_order(order_id)
        cap = _first_capture(o)
        status = o.get("status")
        if cap:
            self.apply_capture(rec, cap, f"get:/v2/checkout/orders/{order_id}")
        elif status == "APPROVED" and rec["state"] == "AWAITING_APPROVAL":
            self.wallet.update_payment(rec["payment_id"], "APPROVED", source="GET order", note="Approved by the buyer")
        elif status == "VOIDED":
            self.wallet.update_payment(rec["payment_id"], "EXPIRED", source="GET order", note="The order was voided")
        elif rec["state"] == "AWAITING_APPROVAL" and time.time() - rec["created_at"] > ORDER_KEY_HOURS * 3600:
            self.wallet.update_payment(rec["payment_id"], "EXPIRED", source="clock", note="Not approved in time; no money moved")

    def apply_capture(self, rec: dict, cap: dict, verified_by: str, *, event_id: str = "", event_type: str = "") -> None:
        amount = _cents((cap.get("amount") or {}).get("value"))
        custom = cap.get("custom_id")
        if not _amount_matches(cap.get("amount"), rec) or (custom and custom != ids.custom_id(rec["digest"])):
            self._anomaly(rec, "What PayPal captured doesn't match what you signed.", event_id, event_type)
            return
        pp = {"capture_id": cap.get("id", "")}
        status = cap.get("status")
        if status == "COMPLETED" and rec["state"] != "PAID":
            self.wallet.update_payment(rec["payment_id"], "PAID", paypal=pp, verified_by=verified_by, source=verified_by.split(":", 1)[0],
                                       event_type=event_type, event_id=event_id, note="Capture completed")
        elif status == "PENDING" and rec["state"] != "CAPTURE_PENDING":
            self.wallet.update_payment(rec["payment_id"], "CAPTURE_PENDING", paypal=pp, source=verified_by.split(":", 1)[0],
                                       event_type=event_type, event_id=event_id, note="PayPal is still processing it")
        elif status in ("DECLINED", "FAILED"):
            self.wallet.update_payment(rec["payment_id"], "DECLINED", paypal=pp, source=verified_by.split(":", 1)[0],
                                       event_type=event_type, event_id=event_id, note=f"Capture {status}")
        else:
            self.wallet.update_payment(rec["payment_id"], paypal=pp, event_id=event_id)

    def order_return(self, rec: dict) -> dict:
        """The buyer came back from approving. Verify, capture once (same key every time), verify again. Idempotent.
        The redirect itself proves nothing: everything here comes from PayPal."""
        with calls.scope(rec["user_id"], rec["session_id"], rec["payment_id"]):
            try:
                self._order_return(rec)
            except PayPalUnknown as e:
                self.mark_unknown(rec, e.reason)
            except PayPalError as e:
                self._capture_refused(rec, e)
            except payments.BadTransition:
                pass
        self._flush()
        return rec

    def _order_return(self, rec: dict) -> None:
        if rec["state"] in payments.TERMINAL and rec["state"] != "PAID":
            return
        if rec["state"] == "PAID":
            return
        o = self.client.get_order(rec["paypal"]["order_id"])
        cap = _first_capture(o)
        if cap:
            self.apply_capture(rec, cap, f"get:/v2/checkout/orders/{rec['paypal']['order_id']}")
            return
        if o.get("status") != "APPROVED":
            return                                 # still waiting: the page says so
        unit = (o.get("purchase_units") or [{}])[0]
        if _cents((unit.get("amount") or {}).get("value")) != rec["amount_cents"] or \
                (unit.get("amount") or {}).get("currency_code") != rec["currency"] or \
                (unit.get("custom_id") and unit.get("custom_id") != ids.custom_id(rec["digest"])):
            self._anomaly(rec, "The order at PayPal doesn't match what you signed.")
            return
        if rec["state"] == "AWAITING_APPROVAL":
            self.wallet.update_payment(rec["payment_id"], "APPROVED", source="GET order", note="Approved by the buyer")
        self._capture(rec)

    def _capture(self, rec: dict) -> None:
        self.wallet.update_payment(rec["payment_id"], "CAPTURING", source="gateway", note="Capturing")
        self.wallet.update_payment(rec["payment_id"], paypal={"stage": "capture"})
        self._flush()
        self.client.capture_order(rec["paypal"]["order_id"], rec["digest"])
        o = self.client.get_order(rec["paypal"]["order_id"])       # verified by GET before anything is reported
        cap = _first_capture(o)
        if not cap:
            raise PayPalUnknown("Capture answered but the order shows no capture yet")
        self.apply_capture(rec, cap, f"get:/v2/checkout/orders/{rec['paypal']['order_id']}")

    def _capture_refused(self, rec: dict, e: PayPalError) -> None:
        if e.has_issue("ORDER_ALREADY_CAPTURED"):
            try:
                o = self.client.get_order(rec["paypal"]["order_id"])
                cap = _first_capture(o)
                if cap:
                    self.apply_capture(rec, cap, f"get:/v2/checkout/orders/{rec['paypal']['order_id']}")
            except (PayPalUnknown, PayPalError):
                self.mark_unknown(rec, "Couldn't re-read the captured order")
        elif e.has_issue("INSTRUMENT_DECLINED"):
            self.wallet.update_payment(rec["payment_id"], "AWAITING_APPROVAL", source="paypal", note="PayPal declined that way of paying. Please try another.")
        elif e.has_issue("ORDER_NOT_APPROVED"):
            if rec["state"] == "CAPTURING":
                self.wallet.update_payment(rec["payment_id"], "AWAITING_APPROVAL", source="paypal", note="Not approved yet")
        elif rec["state"] == "CAPTURING":
            self.wallet.update_payment(rec["payment_id"], "DECLINED", source="paypal", note=f"{e.status} {e.name}"[:120])

    def order_cancelled(self, rec: dict) -> dict:
        """She pressed cancel on the approval page. Only stops what hasn't captured: a GET decides, not the redirect."""
        with calls.scope(rec["user_id"], rec["session_id"], rec["payment_id"]):
            try:
                o = self.client.get_order(rec["paypal"]["order_id"]) if rec["paypal"]["order_id"] else {}
                if _first_capture(o):
                    self.apply_capture(rec, _first_capture(o), f"get:/v2/checkout/orders/{rec['paypal']['order_id']}")
                elif rec["state"] in ("AWAITING_APPROVAL", "APPROVED"):
                    self.wallet.update_payment(rec["payment_id"], "BUYER_CANCELLED", source="buyer", note="Cancelled on the approval page; no money moved")
            except (PayPalUnknown, PayPalError):
                pass
        self._flush()
        return rec

    # ---- Refunds
    def _refresh_refund(self, rec: dict) -> None:
        rid = rec["paypal"]["refund_id"]
        if not rid:
            return
        r = self.client.get_refund(rid)
        self.apply_refund(rec, r, f"get:/v2/payments/refunds/{rid}")

    def apply_refund(self, rec: dict, r: dict, verified_by: str, *, event_id: str = "", event_type: str = "") -> None:
        status = r.get("status")
        if status in ("COMPLETED", None) and not _amount_matches(r.get("amount"), rec):
            self._anomaly(rec, "The refund amount doesn't match what you signed.", event_id, event_type)
            return
        if status == "COMPLETED" and rec["state"] != "REFUNDED":
            self.wallet.update_payment(rec["payment_id"], "REFUNDED", paypal={"refund_id": r.get("id", "")}, verified_by=verified_by,
                                       source=verified_by.split(":", 1)[0], event_type=event_type, event_id=event_id, note="Refund completed")
        elif status in ("FAILED", "CANCELLED"):
            self.wallet.update_payment(rec["payment_id"], "FAILED", source=verified_by.split(":", 1)[0], event_type=event_type,
                                       event_id=event_id, note=f"Refund {status}")

    def _refresh_cancel(self, rec: dict) -> None:
        item_id = rec["paypal"]["item_id"] or self.wallet.payment(rec["parent_payment_id"])["paypal"]["item_id"]
        it = self.client.get_payout_item(item_id)
        status = it.get("transaction_status")
        if status in ("RETURNED", "REFUNDED"):
            if not _amount_matches((it.get("payout_item") or {}).get("amount"), rec, "currency"):
                self._anomaly(rec, "The returned payout amount or currency doesn't match what you signed.")
                return
            self.wallet.update_payment(rec["payment_id"], "REFUNDED", paypal={"item_id": item_id}, verified_by=f"get:/v1/payments/payouts-item/{item_id}",
                                       source="get", note="Payout returned")
        elif status == "SUCCESS":
            self.wallet.update_payment(rec["payment_id"], "FAILED", source="get", note="Too late: they already claimed it")

    # ---- Invoices. Paid only from PAID: MARKED_AS_PAID and PAID_EXTERNAL are the issuer's own say-so.
    def _refresh_invoice(self, rec: dict) -> None:
        pid = rec["paypal"]["invoice_id"]
        if pid:
            self.apply_invoice(rec, self.client.get_invoice(pid), f"get:/v2/invoicing/invoices/{pid}")

    def apply_invoice(self, rec: dict, inv: dict, verified_by: str, *, event_id: str = "", event_type: str = "") -> None:
        to = TERMINAL_INVOICE.get(inv.get("status"))
        if not to or rec["state"] == to:
            return
        if to == "PAID" and not _amount_matches(inv.get("amount"), rec):
            self._anomaly(rec, "The invoice amount doesn't match what you signed.", event_id, event_type)
            return
        self.wallet.update_payment(rec["payment_id"], to, verified_by=verified_by if to == "PAID" else "", source=verified_by.split(":", 1)[0],
                                   event_type=event_type, event_id=event_id, note=f"Invoice status {inv.get('status')}")

    def _verify_request(self, rec: dict) -> None:
        pid = rec["paypal"]["invoice_id"]
        inv = self.client.get_invoice(pid)
        status = inv.get("status")
        if status in ("SENT", "UNPAID", "PARTIALLY_PAID") and rec["state"] in ("SUBMITTING", "UNKNOWN"):
            self.wallet.update_payment(rec["payment_id"], "SENT_TO_PAYER", verified_by=f"get:/v2/invoicing/invoices/{pid}", source="get",
                                       note="Invoice sent")
        elif status in TERMINAL_INVOICE:
            self.apply_invoice(rec, inv, f"get:/v2/invoicing/invoices/{pid}")

    # ------------------------------------------------------------------ unknown outcomes (the matrix above)
    def _resolve_unknown(self, rec: dict) -> None:
        stage = rec["paypal"].get("stage") or {"payouts": "payout_create", "orders": "order_create", "refund": "refund",
                                               "payout_cancel": "payout_cancel", "invoice": "invoice_read",
                                               "money_request": "invoice_create"}[rec["rail"]]
        if stage == "payout_create":
            self._resolve_payout(rec)
        elif stage == "order_create":
            if time.time() - rec["created_at"] > ORDER_KEY_HOURS * 3600:
                self.wallet.update_payment(rec["payment_id"], "ABANDONED", source="clock", note="Never approved; no money moved")
            else:
                self._submit_order(rec)
        elif stage == "capture":
            self._resolve_capture(rec)
        elif stage == "refund":
            if rec["paypal"]["refund_id"]:
                self._refresh_refund(rec)
            else:
                self._submit_refund(rec)
        elif stage == "payout_cancel":
            self._resolve_cancel(rec)
        elif stage in ("invoice_create", "invoice_send"):
            self._resolve_request(rec)
        elif stage == "invoice_read":
            self._submit_bill(rec)

    def _resolve_payout(self, rec: dict) -> None:
        batch = rec["paypal"]["batch_id"]
        if not batch:
            try:
                self._submit_payout(rec)           # the identical body and key: the documented duplicate rule protects it
                return
            except PayPalError as e:
                batch = batch_id_from_links(e)
                if not batch:
                    return                         # stays UNKNOWN; never mint a new key
        self.wallet.update_payment(rec["payment_id"], "PROCESSING", paypal={"batch_id": batch}, source="reconcile")
        self._refresh_payout(self.wallet.payment(rec["payment_id"]))

    def _resolve_capture(self, rec: dict) -> None:
        o = self.client.get_order(rec["paypal"]["order_id"])
        cap = _first_capture(o)
        if cap:
            self.apply_capture(rec, cap, f"get:/v2/checkout/orders/{rec['paypal']['order_id']}")
        elif o.get("status") == "APPROVED":
            self.wallet.update_payment(rec["payment_id"], "CAPTURING", source="reconcile", note="Trying again with the same key")
            try:
                self.client.capture_order(rec["paypal"]["order_id"], rec["digest"])
            except PayPalError as e:
                self._capture_refused(rec, e)
                return
            except PayPalUnknown as e:
                self.mark_unknown(rec, e.reason)
                return
            cap = _first_capture(self.client.get_order(rec["paypal"]["order_id"]))
            if cap:
                self.apply_capture(rec, cap, f"get:/v2/checkout/orders/{rec['paypal']['order_id']}")

    def _resolve_cancel(self, rec: dict) -> None:
        parent = self.wallet.payment(rec["parent_payment_id"])
        item_id = parent["paypal"]["item_id"]
        status = self.client.get_payout_item(item_id).get("transaction_status")
        if status in ("RETURNED", "REFUNDED"):
            self.wallet.update_payment(rec["payment_id"], "REFUNDED", paypal={"item_id": item_id}, verified_by=f"get:/v1/payments/payouts-item/{item_id}",
                                       source="reconcile", note="Payout returned")
        elif status == "UNCLAIMED":
            self._submit_cancel(rec)
        elif status == "SUCCESS":
            self.wallet.update_payment(rec["payment_id"], "FAILED", source="reconcile", note="Too late: they already claimed it")

    def _resolve_request(self, rec: dict) -> None:
        number = ids.invoice_number(rec["digest"])
        found = (self.client.search_invoices({"invoice_number": number}).get("items") or [])
        if not found:
            self._submit_request(rec)
            return
        inv = found[0]
        self.wallet.update_payment(rec["payment_id"], paypal={"invoice_id": inv["id"], "invoice_number": number})
        if inv.get("status") == "DRAFT":
            rec["paypal"]["stage"] = "invoice_send"
            self.client.send_invoice(inv["id"])
        self._verify_request(self.wallet.payment(rec["payment_id"]))

    # ------------------------------------------------------------------ helpers
    def _anomaly(self, rec: dict, why: str, event_id: str = "", event_type: str = "") -> None:
        self.wallet.update_payment(rec["payment_id"], "ANOMALY", source="check", note=why, event_id=event_id, event_type=event_type)
        self.wallet.add_alert(rec["user_id"], "stop", "anomaly", f"Star found a problem with a payment to {rec['counterparty_name']}: {why}", rec["draft_id"])

    def _alert_if_stuck(self, rec: dict) -> None:
        if rec["state"] == "UNKNOWN" and not rec.get("alerted") and time.time() - rec["updated_at"] > settings.unknown_alert_minutes * 60:
            rec["alerted"] = True
            self.wallet.add_alert(rec["user_id"], "warn", "unknown", f"Star still can't confirm a payment to {rec['counterparty_name']}.", rec["draft_id"])

    def _flush(self) -> None:
        if self.audit:
            self.wallet.flush_audit(self.audit)

    def recover_at_startup(self) -> int:
        """Records left mid-call by a crash: nobody knows what happened, so they are UNKNOWN until queried."""
        n = 0
        for rec in list(self.wallet.payments.values()):
            if rec["state"] in ("SUBMITTING", "CAPTURING"):
                self.wallet.update_payment(rec["payment_id"], "UNKNOWN", source="startup", note="The server restarted during this call")
                n += 1
        self._flush()
        return n


def _first_capture(order: dict) -> dict | None:
    for unit in order.get("purchase_units") or []:
        caps = (unit.get("payments") or {}).get("captures") or []
        if caps:
            return caps[0]
    return None


def _id_from_href(href: str) -> str:
    return href.rstrip("/").rsplit("/", 1)[-1] if href else ""


def _category(item: dict) -> str:
    from app.wallet import catalogue
    return (catalogue.offer(item.get("offer_id", "")) or {}).get("paypal_category", "PHYSICAL_GOODS")
