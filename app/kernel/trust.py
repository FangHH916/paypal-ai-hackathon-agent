"""Deterministic checks on who is asking for the money: merchants, listings and invoices. Same style as rules.py: a code
for the audit log, plain words for Mrs. Lee. The model can't call, see or bypass this. It only sees the result.

Everything that came from outside (an invoice note, a merchant's listing text) is treated as data about the sender,
and an attempt to give the assistant instructions counts against the sender (R84).
"""
from __future__ import annotations

import re

from app.kernel.sanitize import has_instruction
from app.models import fmt_money
from app.wallet import catalogue

_PHONE = re.compile(r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b|\bcall us\b|\bcall (this|the) number\b", re.I)
_URGENT = re.compile(r"\burgent|immediately|right away|fraudulent charge|unauthori[sz]ed charge|cancel within|within 24 hours|"
                     r"final notice|account will be (closed|suspended)|legal action", re.I)
_REFUND_SCAM = re.compile(r"overpaid|over-?payment|send (it |that |the (money|difference) )?back|the difference back|refund department|"
                          r"refund (team|agent|department) (called|said|told)|refunded too much", re.I)
NEW_MERCHANT_DAYS = 30
PRICE_ANOMALY = 1.5
USUAL_BILL_FACTOR = 2


def _hit(code: str, level: str, why: str, guardian: bool = False) -> dict:
    return {"code": code, "level": level, "why": why, "guardian": guardian}


def _invoice_checks(inv: dict, wallet, user_id: str, draft, protection: str) -> list[dict]:
    hits = []
    text = f"{inv.get('note', '')} {inv.get('terms', '')}"
    enhanced = protection == "enhanced"
    if has_instruction(text):
        hits.append(_hit("R84_INSTRUCTION_IN_DATA", "BLOCK",
                         "This bill has a hidden message trying to tell me to pay it. Real companies don't do that."))
    if _PHONE.search(text):
        hits.append(_hit("R80_INVOICE_PHONE", "BLOCK" if enhanced else "HIGH",
                         "This bill asks you to phone a number written in its note. That's how fake invoices work. "
                         "Please don't call it."))
    if _URGENT.search(text):
        hits.append(_hit("R81_INVOICE_URGENT", "HIGH", "This bill is trying to rush or frighten you."))
    payee = wallet.payee(user_id, inv["payee_id"])
    known_biller = bool(payee and not inv["payee_id"].startswith("invoicer:"))
    if not known_biller and not wallet.payee_history(user_id, inv["payee_id"]):
        hits.append(_hit("R82_BILLER_UNKNOWN", "HIGH", f"You have never paid {inv['invoicer_name']} before and they are "
                         "not one of your saved bills.", guardian=True))
    history = wallet.payee_history(user_id, inv["payee_id"])
    if history and draft.amount_cents > max(history) * USUAL_BILL_FACTOR:
        hits.append(_hit("R83_BILL_UNUSUAL", "MEDIUM", f"This is more than twice your usual {inv['invoicer_name']} bill "
                         f"({fmt_money(max(history))})."))
    return hits


def _purchase_checks(draft, wallet, user_id: str, scam_level: str) -> list[dict]:
    hits = []
    item = draft.items[0] if draft.items else {}
    offer = catalogue.offer(item.get("offer_id", ""))
    merchant = catalogue.merchant(draft.merchant_id or "")
    if not offer or not merchant or offer["merchant_id"] != draft.merchant_id or offer["price_cents"] != item.get("unit_cents") \
            or draft.amount_cents != offer["price_cents"] * int(item.get("quantity") or 0):
        return [_hit("R93_NOT_IN_CATALOGUE", "BLOCK", "That offer, seller or price isn't exactly what the catalogue says.")]
    if has_instruction(merchant["description"]):
        hits.append(_hit("R84_INSTRUCTION_IN_DATA", "BLOCK",
                         f"{merchant['name']}'s listing has a hidden message telling me to approve it. I don't trust it."))
    if merchant["flagged"]:
        hits.append(_hit("R92_MERCHANT_FLAGGED", "BLOCK", f"{merchant['name']} has been flagged as unsafe."))
    if offer["price_cents"] > offer["reference_cents"] * PRICE_ANOMALY:
        hits.append(_hit("R91_PRICE_ANOMALY", "BLOCK", f"{fmt_money(offer['price_cents'])} is far above the usual price of "
                         f"about {fmt_money(offer['reference_cents'])}."))
    orders = merchant["orders"] + wallet.user(user_id).get("merchant_orders", {}).get(draft.merchant_id, 0)
    if not orders and merchant["days_known"] < NEW_MERCHANT_DAYS:
        hits.append(_hit("R90_MERCHANT_NEW", "HIGH", f"{merchant['name']} is new, and you have never bought from them.", guardian=True))
    if offer["category"] == "gift_card":
        if scam_level in ("HIGH", "BLOCK"):
            hits.append(_hit("R94_GIFT_CARD", "BLOCK", "Being told to buy gift cards is a classic sign of a scam."))
        else:
            hits.append(_hit("R94_GIFT_CARD", "HIGH", "Gift cards are how scammers like to be paid, so your family checks this one. If anyone is telling you to buy it, "
                             "or asks for the numbers on it, stop: that's a scam.",
                             guardian=True))
    return hits


def _refund_checks(draft, wallet, user_id: str) -> list[dict]:
    parent = wallet.payment(draft.parent_payment_id or "")
    if not parent or parent["user_id"] != user_id:
        return [_hit("R95_REFUND_INVALID", "BLOCK", "That payment isn't one of yours.")]
    stoppable = draft.rail == "payout_cancel" and parent["state"] == "UNCLAIMED" and parent["paypal"].get("item_id")
    refundable = draft.rail == "refund" and parent["state"] == "PAID" and parent.get("verified") and parent["paypal"].get("capture_id")
    left = parent["amount_cents"] - parent.get("refunded_cents", 0)
    if not (stoppable or refundable):
        return [_hit("R95_REFUND_INVALID", "BLOCK", "That payment can't be reversed right now.")]
    if draft.amount_cents <= 0 or draft.amount_cents > left:
        return [_hit("R95_REFUND_INVALID", "BLOCK", f"Only {fmt_money(left)} of that payment can still come back.")]
    return []


def check(draft, wallet, *, protection: str = "enhanced", scam_level: str = "LOW", said: str = "") -> dict:
    """{level, codes, reasons, guardian, hits}. Goes on the signed card. rules.evaluate raises the risk level from it."""
    user = draft.user_id
    hits: list[dict] = []
    if draft.kind == "purchase":
        hits = _purchase_checks(draft, wallet, user, scam_level)
    elif draft.kind == "bill" and draft.invoice_id:
        inv = wallet.invoice(user, draft.invoice_id)
        hits = _invoice_checks(inv, wallet, user, draft, protection) if inv else [
            _hit("R95_REFUND_INVALID", "BLOCK", "I can't find that bill.")]
    elif draft.kind == "refund":
        hits = _refund_checks(draft, wallet, user)
    if draft.kind in ("transfer", "bill", "purchase") and _REFUND_SCAM.search(said or ""):
        hits.append(_hit("R96_REFUND_SCAM", "BLOCK", "Someone saying they refunded you too much and asking for the difference back "
                         "is a well-known scam. Real refunds never need you to send money."))
    order = ["LOW", "MEDIUM", "HIGH", "BLOCK"]
    level = max((h["level"] for h in hits), key=order.index, default="LOW")
    return {"level": level, "codes": [h["code"] for h in hits], "reasons": [h["why"] for h in hits],
            "guardian": any(h["guardian"] for h in hits), "hits": hits}


def offer_verdict(offer: dict, wallet, user_id: str, scam_level: str = "LOW") -> dict:
    """What search_catalogue shows for one offer: the same checks, before anything is drafted."""
    from types import SimpleNamespace
    fake = SimpleNamespace(kind="purchase", items=[{"offer_id": offer["offer_id"], "quantity": 1, "unit_cents": offer["price_cents"]}],
                           merchant_id=offer["merchant_id"], amount_cents=offer["price_cents"], user_id=user_id)
    hits = _purchase_checks(fake, wallet, user_id, scam_level)
    level = "stop" if any(h["level"] == "BLOCK" for h in hits) else "family" if any(h["guardian"] for h in hits) else \
        "check" if hits else "ok"
    return {"verdict": level, "codes": [h["code"] for h in hits], "reasons": [h["why"] for h in hits]}
