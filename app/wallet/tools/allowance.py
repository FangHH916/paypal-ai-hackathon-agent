"""The spending allowance. Star can't see a PayPal balance (it isn't generally available); this is the limit she and her
family set, held locally and enforced by rule R03."""
from __future__ import annotations

from app.models import fmt_money
from app.wallet.tools.base import NoArgs, Tool


def get_allowance(gw, s, _):
    left = gw.wallet.allowance_left(s.user_id)
    monthly = gw.wallet.user(s.user_id)["allowance"]["monthly_cents"]
    return {"left": fmt_money(left), "monthly_allowance": fmt_money(monthly),
            "note": "This is the allowance she and her family set, not a PayPal balance. Star can't see PayPal balances."}


TOOLS = [
    Tool("get_allowance", "read", "How much of the customer's Star spending allowance is left. It is the limit she and her "
         "family set, not a PayPal balance: never call it a balance at PayPal.", NoArgs, get_allowance, api="Star · allowance"),
]
