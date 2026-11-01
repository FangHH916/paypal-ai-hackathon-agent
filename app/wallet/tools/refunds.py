"""Getting money back, stopping an unclaimed payout, and reading cases and payment status.

Reads come from verified records (never a redirect or the model's memory). A refund or a stopped payout is still a signed
card. Honest copy: in this demo the merchants are ours, so a refund is immediate. With an outside merchant Star would
help her ask them or open a case with PayPal.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.kernel.executor import KernelError
from app.models import fmt_money
from app.paypal.errors import PayPalConfigError, PayPalError, PayPalUnknown
from app.wallet.payments import status_of
from app.wallet.tools.base import EVIDENCE_DESC, NoArgs, Tool


class PaymentArgs(BaseModel):
    payment_id: str = Field(description="Must be a payment_id returned by list_refundable or payment_status")
    evidence: str = Field(default="", max_length=200, description=EVIDENCE_DESC)


def _label(rec: dict) -> str:
    items = rec["card"].get("items") or []
    return items[0]["name"] if items else rec["counterparty_name"]


def _left(rec: dict) -> int:
    return rec["amount_cents"] - rec.get("refunded_cents", 0)


def list_refundable(gw, s, _):
    rows = []
    for r in gw.wallet.payments_for(s.user_id):
        if r["rail"] == "orders" and r["state"] == "PAID" and r["verified"] and _left(r) > 0:
            rows.append({"payment_id": r["payment_id"], "what": _label(r), "to": r["counterparty_name"], "amount": fmt_money(_left(r)), "how": "refund"})
        elif r["rail"] == "payouts" and r["state"] == "UNCLAIMED":
            rows.append({"payment_id": r["payment_id"], "what": f"Payment to {r['counterparty_name']}", "to": r["counterparty_name"],
                         "amount": fmt_money(r["amount_cents"]), "how": "stop, because it has not been claimed yet"})
    return rows


def draft_refund(gw, s, a: PaymentArgs):
    rec = gw.wallet.payment(a.payment_id)
    if not rec or rec["user_id"] != s.user_id or rec["rail"] != "orders":
        raise KernelError(f"payment_id={a.payment_id} is not a purchase of this customer. Call list_refundable.")
    d = gw.kernel.create_draft(s, kind="refund", parent_payment_id=a.payment_id, evidence=a.evidence, verification=gw._verification(s))
    return gw._draft_result(d)


def draft_stop_payout(gw, s, a: PaymentArgs):
    rec = gw.wallet.payment(a.payment_id)
    if not rec or rec["user_id"] != s.user_id or rec["rail"] != "payouts":
        raise KernelError(f"payment_id={a.payment_id} is not a payment of this customer. Call list_refundable.")
    d = gw.kernel.create_draft(s, kind="refund", parent_payment_id=a.payment_id, evidence=a.evidence, verification=gw._verification(s))
    return gw._draft_result(d)


def payment_status(gw, s, _):
    """Local verified state only. The agent never triggers PayPal writes or reconciliation by asking."""
    label = gw.kernel.gateway.mode_status()["label"]
    return [{"payment_id": r["payment_id"], "what": _label(r), "amount": fmt_money(r["amount_cents"]), "state": r["state"],
             "result": status_of(r), "verified": r["verified"], "mode": label,
             "confirmed_by": "The simulator" if str(label).upper().startswith("SIM") else "PayPal"} for r in gw.wallet.payments_for(s.user_id)[:8]]


def list_disputes(gw, s, _):
    try:
        rows = gw.kernel.gateway.reader.list_disputes().get("items") or []
    except (PayPalUnknown, PayPalError, PayPalConfigError):
        return {"available": False, "note": "PayPal cases can't be read right now."}
    return {"available": True, "cases": [{"id": d.get("dispute_id"), "state": d.get("dispute_state") or d.get("status"), "reason": d.get("reason"),
                                          "amount": (d.get("dispute_amount") or {}).get("value")} for d in rows],
            "note": "Read only: Star can't accept or fight a case. In this demo these are cases on the household's own account."}


TOOLS = [
    Tool("list_refundable", "read", "Purchases that can be refunded and payouts that can still be stopped, from verified records.",
         NoArgs, list_refundable, api="Star · verified payments"),
    Tool("draft_refund", "draft", "Create a draft to refund a purchase, for the user to check and sign. The amount comes from the "
         "signed purchase. You cannot refund it yourself.", PaymentArgs, draft_refund, api="PayPal Payments · draft only, nothing is sent"),
    Tool("draft_stop_payout", "draft", "Create a draft to stop a payment nobody has claimed yet, for the user to check and sign.",
         PaymentArgs, draft_stop_payout, api="PayPal Payouts · draft only, nothing is sent"),
    Tool("payment_status", "read", "What really happened to the latest payments, from verified state. Use it for 'did it go "
         "through?'. Only say something was paid or sent if it says verified.", NoArgs, payment_status, api="Star · verified payments"),
    Tool("list_disputes", "read", "Open PayPal cases. Read only.", NoArgs, list_disputes, api="PayPal Disputes · GET /v1/customer/disputes", group="disputes"),
]
