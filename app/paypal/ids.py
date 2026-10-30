"""Idempotency keys: pure functions of the signed card's digest (invariant I4).

So a key can't be minted again by a retry, a restart or a bug: the same signature always produces the same PayPal
request, and anyone can check offline that a receipt's key came from what the customer signed (scripts/verify_receipt.py
repeats these functions on purpose, so it can run without this package).
Limits, from the OpenAPI specs and docs: sender_batch_id 256, sender_item_id 63, invoice_number 25, custom_id 255,
purchase_units invoice_id 127. The Orders PayPal-Request-Id limit (108) comes from the docs; these stay well under it.
"""
from __future__ import annotations


def sender_batch_id(digest: str) -> str:
    return "star-" + digest[:40]


def sender_item_id(digest: str) -> str:
    return digest[:32]


def order_key(digest: str) -> str:
    return "ord-" + digest[:40]


def capture_key(digest: str) -> str:
    return "cap-" + digest[:40]


def refund_key(digest: str) -> str:
    return "ref-" + digest[:40]


def invoice_number(digest: str) -> str:
    return "STAR-" + digest[:16]


def custom_id(digest: str) -> str:
    return digest[:64]


def order_invoice_id(digest: str) -> str:
    return "STAR-" + digest[:20]


def key_for(rail: str, digest: str) -> str:
    return {"payouts": sender_batch_id, "orders": order_key, "refund": refund_key, "payout_cancel": refund_key,
            "invoice": invoice_number, "money_request": invoice_number}[rail](digest)
