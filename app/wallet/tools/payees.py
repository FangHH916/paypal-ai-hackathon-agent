"""Saved people and billers; adding a new person (signed by the customer, never by the agent alone)."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.wallet.tools.base import EVIDENCE_DESC, NoArgs, Tool


class AddPayeeArgs(BaseModel):
    name: str = Field(max_length=60, description="The person's name exactly as the user said it")
    email: str = Field(max_length=120, description="Their PayPal email, exactly as the user said it")
    relation: str = Field(default="", max_length=30, description="Who they are to the user, e.g. friend; empty if not said")
    evidence: str = Field(default="", max_length=200, description=EVIDENCE_DESC)


def list_payees(gw, s, _):
    return [{"payee_id": p["payee_id"], "name": p["name"], "relation": p["relation"], "type": p["type"],
             "aliases": p["aliases"], "account": p["masked_account"]} for p in gw.wallet.payees(s.user_id)]


def request_add_payee(gw, s, a: AddPayeeArgs):
    card = gw.kernel.request_change(s, "add_payee", a.model_dump(), a.evidence)
    return {"change_id": card["change_id"], "status": "A confirmation card is now shown. The user checks the email and "
            "confirms with their fingerprint; nothing is added before that. Tell them in one or two simple sentences."}


TOOLS = [
    Tool("list_payees", "read", "The user's saved people (aliases such as 'daughter') and billers. Must be called before a "
         "transfer or a money request; use the returned payee_id.", NoArgs, list_payees, api="Star · saved people"),
    Tool("request_add_payee", "draft", "Ask to add a NEW person the user named, with the PayPal email they said. Shows a "
         "confirmation card the user must sign; the agent can't add anyone alone.",
         AddPayeeArgs, request_add_payee, api="Star · add a person (draft)", group="add_payee"),
]
