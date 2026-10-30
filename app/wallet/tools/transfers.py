"""Send money to family: a draft that becomes a PayPal Payout only after the customer signs.

The agent only drafts. Everything after that is the kernel's: evidence grounding, the independent verifier, the rules,
the code-built card, the customer's signature, and the execution gateway.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.kernel.executor import KernelError
from app.models import fmt_money, to_cents
from app.wallet.tools.base import EVIDENCE_DESC, MEANT_DESC, MISHEARD_DESC, Tool


class DraftTransferArgs(BaseModel):
    payee_id: str = Field(description="Must be a payee_id returned by list_payees")
    amount: float = Field(gt=0, description="Amount in dollars, e.g. 200 or 50.5")
    evidence: str = Field(default="", max_length=200, description=EVIDENCE_DESC)
    misheard: str = Field(default="", max_length=40, description=MISHEARD_DESC)
    meant: str = Field(default="", max_length=40, description=MEANT_DESC)
    note: str = Field(default="", max_length=60)


def draft_transfer(gw, s, a: DraftTransferArgs):
    payee = gw.wallet.payee(s.user_id, a.payee_id)
    if payee is None:
        raise KernelError(f"payee_id={a.payee_id} is not in the user's saved payees. Only use a payee_id returned by "
                          "list_payees; to pay someone new, the user can add them first (request_add_payee).")
    if payee["type"] == "biller":
        raise KernelError("This payee is a biller. Use list_invoices + draft_bill_payment instead.")
    gw._run_judge(s, f"Transfer {fmt_money(to_cents(a.amount))} to {payee['name']} ({payee['relation']})")
    d = gw.kernel.create_draft(s, kind="transfer", payee_id=a.payee_id, amount_cents=to_cents(a.amount), note=a.note,
                               evidence=a.evidence, verification=gw._verification(s), heard=(a.misheard, a.meant))
    return gw._draft_result(d)


TOOLS = [
    Tool("draft_transfer", "draft",
         "Create a draft to send money to a saved person, for the user to check on a confirmation card and authorize "
         "themselves. You cannot send money, only draft it.", DraftTransferArgs, draft_transfer,
         api="PayPal Payouts · draft only, nothing is sent"),
]
