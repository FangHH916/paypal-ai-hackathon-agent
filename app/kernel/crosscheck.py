"""Dual-engine semantic audit, deterministic half: compare the draft with what the independent verifier extracted.

Engine 1 (agent/loop.py + tools.py) understands the request and drafts the transaction.
Engine 2 (agent/verifier.py) is a separate, read-only model call that sees ONLY the customer's own words (the
speech-to-text transcript) - never the draft - and extracts: intent, payee or item, amount.

This module, which is plain code, compares the two field by field. Any field the customer stated that disagrees with
the draft freezes it (Phase.FROZEN): it can never be signed or executed, and the customer is told exactly what
didn't match. A field the customer didn't state is "not stated", not a mismatch (evidence grounding in
evidence.py and the rules handle that, e.g. "the usual amount").
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.models import Draft, fmt_money
from app.wallet import catalogue

_PAYMENT_INTENTS = {"transfer": "transfer", "payment": "transfer", "bill": "bill", "bill_pay": "bill", "bill_payment": "bill",
                    "purchase": "purchase", "buy": "purchase", "refund": "refund", "stop_payout": "refund",
                    "money_request": "money_request", "request": "money_request"}
_INTENT_WORD = {"transfer": "sending money", "bill": "paying a bill", "purchase": "a purchase", "refund": "getting money back",
                "money_request": "asking someone to pay you"}


def _words(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9&]+", " ", str(text).lower()).strip() + " "


def _names(entity: dict) -> list[str]:
    return [n for n in [entity.get("name", ""), entity.get("title", ""), *entity.get("aliases", [])] if n]


def _pool(draft: Draft, wallet) -> list[tuple[str, list[str]]]:
    if draft.kind == "purchase":
        out = [(o["merchant_id"], _names(o)) for o in catalogue.OFFERS]
        return out + [(m["merchant_id"], [m["name"]]) for m in catalogue.MERCHANTS.values()]
    if draft.kind == "refund":
        parent = wallet.payment(draft.parent_payment_id or "") or {}
        names = [parent.get("counterparty_name", ""), *[i["name"] for i in parent.get("card", {}).get("items", [])]]
        for item in parent.get("card", {}).get("items", []):
            offer = catalogue.offer(item.get("offer_id", "")) or {}
            names += offer.get("aliases", [])
        payee = wallet.payee(draft.user_id, parent.get("card", {}).get("payee_id", "")) or {}
        return [(draft.payee_id, [n for n in names + _names(payee) if n])]
    pool = [(p["payee_id"], _names(p)) for p in wallet.payees(draft.user_id)]
    return pool + [(i["payee_id"], [i["title"], i["invoicer_name"]]) for i in wallet.invoices(draft.user_id, only_open=False)]


def resolve_targets(said: str, draft: Draft, wallet) -> set[str]:
    """Which saved payees / invoices / merchants could the customer's phrase refer to?"""
    w = _words(said)
    hits = set()
    for pid, names in _pool(draft, wallet):
        for n in names:
            nw = _words(n)
            if nw.strip() and (nw in w or (len(w.strip()) > 2 and w in nw)):
                hits.add(pid)
    return hits


def compare(draft: Draft, extracted: dict, wallet) -> dict:
    fields: dict[str, str] = {}
    why: list[str] = []
    ex = extracted if isinstance(extracted, dict) else {}
    intent = _PAYMENT_INTENTS.get(str(ex.get("intent") or "none").lower())
    said_payee, said_amount = ex.get("payee"), ex.get("amount")

    # ---- Is there a request at all, and of the same type?
    if not intent or (not said_payee and said_amount in (None, "") and intent != "refund"):
        fields["request"] = "mismatch"
        why.append("The second reading found no payment request in what you said.")
    elif intent != draft.kind:
        fields["type"] = "mismatch"
        why.append(f"Type: you asked about {_INTENT_WORD[intent]}, but this is {_INTENT_WORD[draft.kind]}.")
    else:
        fields["type"] = "match"

    # ---- Who / what
    if said_payee is not None and not isinstance(said_payee, str):
        fields["payee"] = "mismatch"
        why.append("The second reading returned an invalid recipient.")
    elif said_payee:
        targets = resolve_targets(str(said_payee), draft, wallet)
        ok = draft.payee_id in targets
        fields["payee"] = "match" if ok else "mismatch"
        if not ok:
            label = f"“{said_payee}”"
            if len(targets) == 1 and draft.kind == "transfer":
                p = wallet.payee(draft.user_id, next(iter(targets))) or {}
                if p.get("type") == "person":
                    label = f"your {p['relation']} {p['name']}"
            why.append(f"Who: you asked for {label}, but this is for {draft.payee_name}.")
    elif draft.kind == "refund":
        fields["payee"] = "not stated"        # "I want my money back": which one is on the card she signs
    else:
        fields["payee"] = "mismatch"
        why.append("The second reading could not confirm the recipient or item.")

    # ---- How much
    cents = None
    invalid = False
    if said_amount not in (None, ""):
        try:
            if isinstance(said_amount, bool) or not isinstance(said_amount, (int, float, str)):
                raise ValueError("Invalid amount type")
            amount = Decimal(str(said_amount).replace(",", "").replace("$", ""))
            if not amount.is_finite() or amount <= 0 or amount * 100 != (amount * 100).to_integral_value():
                raise ValueError("Invalid amount")
            cents = int(amount * 100)
        except (ValueError, InvalidOperation):
            invalid = True
    if invalid:
        fields["amount"] = "mismatch"
        why.append("The second reading returned an invalid amount; please say the amount again.")
    elif cents is not None:
        ok = cents == draft.amount_cents
        fields["amount"] = "match" if ok else "mismatch"
        if not ok:
            why.append(f"Amount: you said {fmt_money(cents)}, but this is for {fmt_money(draft.amount_cents)}.")
    elif draft.grounding.get("amount_source") == "user":
        fields["amount"] = "mismatch"
        why.append("The second reading could not confirm the amount you stated.")
    else:
        fields["amount"] = "not stated"

    # ---- How many (purchases)
    qty = ex.get("quantity")
    if draft.kind == "purchase" and qty not in (None, ""):
        have = sum(i.get("quantity", 0) for i in draft.items)
        ok = str(qty).strip().isdigit() and int(str(qty).strip()) == have
        fields["quantity"] = "match" if ok else "mismatch"
        if not ok:
            why.append(f"How many: you said {qty}, but this is for {have}.")

    return {"status": "mismatch" if why else "match", "fields": fields, "mismatches": why}
