"""PaymentRecord: the life of one signed payment, after the gateway accepted it.

A record is written BEFORE the HTTP request goes out (invariant I3) and its keys are functions of the signed digest
(I4), so a crash or a timeout can always be resolved by asking PayPal about the same thing. States only move along the
paths below. A success state can only be set with verified evidence (I5): a signature-verified webhook or an
authenticated GET. A redirect, a query string or a browser message can never set one.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

SUCCESS = {"SENT", "PAID", "REFUNDED"}
# Nothing further will happen on its own. (DECLINED etc. release the allowance; see RELEASES.)
TERMINAL = {"SENT", "PAID", "REFUNDED", "RETURNED", "REVERSED", "FAILED", "DECLINED", "BUYER_CANCELLED", "EXPIRED",
            "ABANDONED", "INVOICE_CANCELLED", "ANOMALY"}
RELEASES = {"FAILED", "RETURNED", "REVERSED", "DECLINED", "BUYER_CANCELLED", "EXPIRED", "ABANDONED", "INVOICE_CANCELLED"}
# Only these kinds spend from the allowance
SPENDS = {"transfer", "purchase", "bill"}

_AFTER_WRITE = {"PROCESSING", "UNCLAIMED", "SENT", "AWAITING_APPROVAL", "AWAITING_PAYMENT", "SENT_TO_PAYER",
                "REFUND_PENDING", "REFUNDED", "RETURNED", "FAILED", "UNKNOWN", "ANOMALY", "HELD", "ABANDONED"}
ALLOWED = {
    "SUBMITTING": _AFTER_WRITE,
    "UNKNOWN": _AFTER_WRITE | {"APPROVED", "CAPTURING", "CAPTURE_PENDING", "PAID", "DECLINED"},
    "PROCESSING": {"SENT", "UNCLAIMED", "FAILED", "HELD", "RETURNED", "ANOMALY"},
    "HELD": {"SENT", "UNCLAIMED", "FAILED", "RETURNED", "ANOMALY"},
    "UNCLAIMED": {"SENT", "RETURNED", "FAILED", "ANOMALY"},
    "AWAITING_APPROVAL": {"APPROVED", "BUYER_CANCELLED", "EXPIRED", "CAPTURING", "CAPTURE_PENDING", "PAID", "DECLINED", "ANOMALY"},
    "APPROVED": {"CAPTURING", "CAPTURE_PENDING", "PAID", "DECLINED", "AWAITING_APPROVAL", "ANOMALY"},
    "CAPTURING": {"PAID", "CAPTURE_PENDING", "DECLINED", "UNKNOWN", "AWAITING_APPROVAL", "APPROVED", "ANOMALY"},
    "CAPTURE_PENDING": {"PAID", "DECLINED", "ANOMALY"},
    "AWAITING_PAYMENT": {"PAID", "INVOICE_CANCELLED", "ANOMALY"},
    "SENT_TO_PAYER": {"PAID", "INVOICE_CANCELLED", "ANOMALY"},
    "REFUND_PENDING": {"REFUNDED", "FAILED", "ANOMALY"},
    # Terminal states don't reopen, except money later coming back
    "SENT": {"RETURNED", "REVERSED"},
    "PAID": {"REFUNDED", "REVERSED"},
}


class BadTransition(Exception):
    pass


def mask_email(email: str) -> str:
    """a***@family.example: enough for a person to recognise, nothing for the audit log to leak (I8)."""
    if not email or "@" not in email:
        return email or ""
    local, domain = email.split("@", 1)
    return f"{local[:1]}***@{domain}"


def masked_card(card: dict) -> dict:
    return {**card, "payee_handle": mask_email(card.get("payee_handle", ""))}


def can_move(state: str, to: str) -> bool:
    return to == state or to in ALLOWED.get(state, ())


def apply(rec: dict, to: str, *, verified_by: str = "", source: str = "", event_type: str = "", note: str = "",
          now: float | None = None) -> bool:
    """Move a record to a state, adding the evidence entry. Returns False when nothing changed. Raises BadTransition on
    a move that isn't allowed, and refuses a success state with no verified evidence."""
    state = rec["state"]
    if to == state and not verified_by:
        return False
    if not can_move(state, to):
        raise BadTransition(f"{state} -> {to}")
    if to in SUCCESS and not verified_by:
        raise BadTransition(f"{to} needs verified evidence")
    now = now or time.time()
    rec["state"], rec["updated_at"] = to, now
    if verified_by:
        rec["verified"], rec["verified_by"], rec["verified_at"] = True, verified_by, now
    elif to != state:
        rec["verified"] = False
    entry = {"at": now, "state": to}
    for k, v in (("source", source), ("event_type", event_type), ("verified_by", verified_by), ("note", note)):
        if v:
            entry[k] = v
    rec["evidence"].append(entry)
    return True


def status_of(rec: dict) -> str:
    """The word on the receipt. Never SUCCESS without verified state."""
    state = rec["state"]
    if state in SUCCESS and rec.get("verified"):
        return "SUCCESS"
    if state == "UNKNOWN":
        return "UNKNOWN"
    return "FAILED" if state in RELEASES or state == "ANOMALY" else "PENDING"


def new_record(*, payment_id: str, user_id: str, session_id: str, card: dict, digest: str, rail: str,
               idempotency_key: str, authorization: dict | None, verification: dict | None, decision: dict | None,
               now: float | None = None) -> dict:
    now = now or time.time()
    return {"payment_id": payment_id, "user_id": user_id, "session_id": session_id, "draft_id": card["draft_id"],
            "digest": digest, "kind": card["kind"], "rail": rail, "amount_cents": card["amount_cents"],
            "currency": card["currency"], "counterparty_name": card["payee_name"],
            "counterparty_handle": card.get("payee_handle", ""), "idempotency_key": idempotency_key,
            "state": "SUBMITTING", "verified": False, "verified_by": "", "verified_at": None,
            "created_at": now, "updated_at": now, "reconcile_attempts": 0, "next_reconcile_at": 0.0,
            "paypal": {"batch_id": "", "item_id": "", "order_id": "", "capture_id": "", "refund_id": "", "invoice_id": "",
                       "invoice_number": card.get("invoice_number") or "", "approve_url": "", "debug_ids": []},
            "parent_payment_id": card.get("parent_payment_id"), "refunded_cents": 0, "released": False, "event_ids": [],
            "evidence": [{"at": now, "state": "SUBMITTING", "source": "gateway", "note": "write-ahead record"}],
            "card": card, "authorization": authorization or {"method": "demo authenticator (unsigned)"},
            "verification": verification or {}, "decision": decision, "dispute": None}


def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def receipt_body(rec: dict, mode: str) -> dict:
    """receipt_version 3. It proves that THIS signature produced THIS PayPal request key (scripts/verify_receipt.py).
    It does not prove PayPal's state: that is what verified_by records, and PayPal itself remains the authority."""
    pp = rec["paypal"]
    body = {"receipt_version": 3, "txn_id": rec["payment_id"], "status": status_of(rec), "executed_at": int(rec["created_at"]),
            "card": rec["card"], "digest": rec["digest"], "verification": rec["verification"],
            "authorization": rec["authorization"],
            "paypal": {"mode": mode, "api": rec["rail"], "idempotency_key": rec["idempotency_key"],
                       "ids": {k: v for k, v in pp.items() if v and k not in ("approve_url", "debug_ids", "stage", "return_base")},
                       "state": rec["state"], "verified_by": rec["verified_by"], "verified_at": rec["verified_at"],
                       "event_ids": list(rec["event_ids"])}}
    if rec.get("decision"):
        body["decision"] = rec["decision"]
    body["receipt_sha256"] = hashlib.sha256(_canonical(body).encode()).hexdigest()
    return body


_KIND_WORD = {"transfer": "Money sent", "bill": "Bill", "purchase": "Purchase", "refund": "Refund", "money_request": "Money request"}


def activity_row(rec: dict, audit_hash: str = "") -> dict:
    """One row of the Activity & audit grid."""
    money_out = rec["kind"] in SPENDS
    guardian = (rec.get("decision") or {}).get("guardian_approval")
    auth = rec.get("authorization") or {}
    sign = -1 if money_out else 1
    return {"row_id": rec["payment_id"], "time": rec["created_at"], "updated": rec["updated_at"], "type": _KIND_WORD.get(rec["kind"], rec["kind"]),
            "kind": rec["kind"], "to": rec["counterparty_name"], "handle": mask_email(rec.get("counterparty_handle", "")),
            "amount_cents": sign * rec["amount_cents"], "currency": rec["currency"], "status": rec["state"],
            "verified": bool(rec.get("verified")), "verified_by": rec.get("verified_by") or "",
            "stopped_by": "", "why": rec["evidence"][-1].get("note", ""), "reference": next(
                (rec["paypal"].get(k) for k in ("capture_id", "item_id", "refund_id", "invoice_id", "order_id", "batch_id")
                 if rec["paypal"].get(k)), ""),
            "signed_with": {"passkey": "Passkey", "device_key": "Browser key"}.get(auth.get("method", ""), auth.get("method", "")),
            "family": "Approved" if guardian else "", "audit_hash": audit_hash[:16], "outcome": "moved" if rec["state"] in SUCCESS else "open",
            "digest": rec["digest"]}


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
