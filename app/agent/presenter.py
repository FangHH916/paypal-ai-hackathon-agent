"""Turn a draft into a confirmation card and a spoken read-back.

The card is generated entirely by code from the draft and never rewritten by the model: what the user sees,
what they sign, and what gets executed are the same data.
"""
from __future__ import annotations

import re
import time

from app.agent.textparse import amount_digits_spoken, amount_words_en
from app.models import Draft, Phase, RiskLevel, fmt_money
from app.wallet.ledger import Wallet
from app.wallet.payments import mask_email

_TITLES = {"transfer": "Confirm sending money", "bill": "Confirm paying this bill", "purchase": "Confirm this purchase",
           "refund": "Confirm getting your money back", "money_request": "Confirm asking for this money"}


def _readback(d: Draft, payee: dict, words: str) -> str:
    m = fmt_money(d.amount_cents)
    if d.kind == "purchase":
        item = d.items[0] if d.items else {"name": "this", "quantity": 1}
        return f"You want to buy {item['quantity']} {item['name']} from {d.merchant_name} for {words} ({m}). Is that right?"
    if d.kind == "refund":
        if d.rail == "payout_cancel":
            return f"You want to stop your payment of {words} ({m}) to {d.payee_name}, so the money comes back to you. Is that right?"
        return f"You want your money back: {words} ({m}) from {d.payee_name}. Is that right?"
    if d.kind == "money_request":
        return f"You want to ask {d.payee_name} to pay you {words} ({m}). Is that right?"
    relation = payee.get("relation", "")
    who = f"your {relation} {d.payee_name}" if relation and payee.get("type") == "person" else d.payee_name
    verb = "send" if d.kind == "transfer" else "pay"
    return f"You want to {verb} {words} ({m}) to {who}. Is that right?"


def _short_readback(d: Draft) -> str:
    m = fmt_money(d.amount_cents)
    if d.kind == "refund":
        return f"{m} back from {d.payee_name}."
    if d.kind == "money_request":
        return f"Ask {d.payee_name} for {m}."
    return f"{m} to {d.payee_name}."


def _next_step_words(d: Draft) -> str:
    return {"purchase": "After you confirm, PayPal asks you to approve it. It is only paid once PayPal says so.",
            "bill": "After you confirm, you pay it on PayPal's own page.",
            "transfer": "After you confirm, PayPal sends it. I'll tell you when PayPal says it arrived.",
            "refund": "After you confirm, I ask PayPal for it back and tell you when it is done.",
            "money_request": "After you confirm, PayPal sends them a request in your name."}[d.kind]


def build_card(d: Draft, wallet: Wallet, phase: Phase, care: bool = True, offers: list | None = None, mode_label: str = "") -> dict:
    payee = wallet.payee(d.user_id, d.payee_id) or {}
    relation = payee.get("relation", "")
    is_person = payee.get("type") == "person"
    words = amount_words_en(d.amount_cents)
    risk = d.risk
    v = d.verification or {}
    checks = d.decision.get("checks", {})
    invoice = wallet.invoice(d.user_id, d.invoice_id) if d.invoice_id else None
    return {
        "draft_id": d.draft_id,
        "digest": d.digest,
        "kind": d.kind,
        "rail": d.rail,
        "title": ("Confirm stopping this payment" if d.rail == "payout_cancel" else _TITLES[d.kind]),
        "paypal_mode": mode_label,
        "amount_note": (f"{d.grounding['heard'][0].upper()}{d.grounding['heard'][1:]}. Please check the amount."
                        if d.grounding.get("heard") else
                        f"{d.grounding['derived']}, worked out by Star" if d.grounding.get("amount_source") == "derived"
                        else "the price in the catalogue" if d.grounding.get("amount_source") == "catalogue"
                        else "the amount on the invoice" if d.grounding.get("amount_source") == "bill"
                        else "what you paid" if d.grounding.get("amount_source") == "payment" else ""),
        "payee_name": d.payee_name,
        "payee_relation": relation if is_person else "",
        "payee_account": mask_email(d.payee_handle) if d.payee_handle else payee.get("masked_account", ""),
        "items": d.items,
        "invoice": {"number": invoice["number"], "title": invoice["title"], "due": invoice["due"]} if invoice else None,
        "merchant": d.merchant_name,
        "trust": {k: d.trust.get(k) for k in ("level", "codes", "reasons", "guardian")} if d.trust else {},
        "offers_checked": offers or [],
        "next_step_words": _next_step_words(d),
        "verification": {"status": v.get("status", "missing"), "fields": v.get("fields", {}),
                         "mismatches": v.get("mismatches", []), "engine": v.get("engine", ""),
                         "independent": "offline" not in v.get("engine", "").lower()},
        "decision": d.decision,
        "guardian_required": checks.get("guardian_approval", {}).get("status") == "pending",
        "guardian_denied": checks.get("guardian_approval", {}).get("status") == "fail",
        "guardian_approved": checks.get("guardian_approval", {}).get("status") == "pass",
        "amount": fmt_money(d.amount_cents),
        "amount_words": words,
        "amount_spoken_digits": amount_digits_spoken(d.amount_cents),
        "from_account": "Star allowance",
        "risk_level": risk.level.value if risk else "LOW",
        "reasons": risk.reasons if risk else [],
        "verify_question": risk.verify_question if risk else None,
        "verify_kind": risk.verify_kind if risk else None,
        "cooldown_left": max(0, int(d.cooling_until - time.time())) if phase == Phase.COOLING else 0,
        "can_confirm": phase in (Phase.AWAITING_AUTH, Phase.COOLING) and (not risk or risk.level != RiskLevel.BLOCK)
                       and v.get("status") == "match"
                       and checks.get("guardian_approval", {}).get("status") not in ("pending", "fail")
                       and checks.get("plan", {}).get("status") != "fail",
        "readback": _readback(d, payee, words) if care else _short_readback(d),
        "expires_at": int(d.expires_at),
        "you_said": d.you_said,
        "you_said_audio": d.you_said_audio,
        "amount_from_user": d.grounding.get("amount", True),
        "amount_source": d.grounding.get("amount_source", "user"),
        "payee_from_user": d.grounding.get("payee", True),
    }


def source_words(text: str, label: str = "") -> str:
    """In simulator mode nothing came from PayPal, so Star doesn't say it did."""
    if not str(label).upper().startswith("SIM"):
        return text
    text = re.sub(r"(^|[.!?]\s+)PayPal\b", lambda m: m.group(1) + "The simulator", text)
    return text.replace("PayPal", "the simulator")


def receipt_text(d: Draft, receipt: dict) -> str:
    """What Star says after signing, in the words of whoever really answered."""
    return source_words(_receipt_words(d, receipt), receipt.get("paypal_mode", ""))


def _receipt_words(d: Draft, receipt: dict) -> str:
    """Success words only from verified state: a redirect or a promise is never enough."""
    state, who, m = receipt["state"], d.payee_name, fmt_money(d.amount_cents)
    verified = receipt.get("verified")
    if state == "UNKNOWN":
        return "I can't confirm that went through yet. Please don't pay again. I'm checking with PayPal."
    if state == "ANOMALY":
        return "PayPal's answer didn't match what you signed, so I've stopped and told your family. Nothing will be reported as paid."
    if state == "AWAITING_APPROVAL":
        return f"Signed. Now please approve {m} to {who} on PayPal."
    if state == "AWAITING_PAYMENT":
        return f"Signed. Now please pay {m} to {who} on PayPal's page."
    if state == "SENT_TO_PAYER":
        return f"Done. PayPal sent {who} your request for {m}."
    if state == "SENT" and verified:
        return f"Done. PayPal confirms {who} has received {m}."
    if state == "UNCLAIMED":
        return f"PayPal has sent {m}, and it's waiting for {who} to claim it. You can still stop this."
    if state == "PROCESSING":
        return f"Sent to PayPal: {m} to {who}. Checking with PayPal now."
    if state == "PAID" and verified:
        return f"Done. PayPal confirms you've paid {m} to {who}."
    if state == "REFUNDED" and verified:
        return f"Done. PayPal confirms {m} is on its way back to you."
    if state == "REFUND_PENDING":
        return f"PayPal is working on your {m}. I'll tell you when it's done."
    if state == "BUYER_CANCELLED":
        return "You cancelled it on the approval page. No money moved."
    if state in ("FAILED", "DECLINED", "EXPIRED", "ABANDONED", "INVOICE_CANCELLED"):
        return f"That didn't go through, so {m} stays with you. PayPal's answer: {state.replace('_', ' ').lower()}."
    if state in ("RETURNED", "REVERSED"):
        return f"PayPal says the {m} to {who} came back or was reversed."
    return f"{who}: {state.replace('_', ' ').lower()}."
