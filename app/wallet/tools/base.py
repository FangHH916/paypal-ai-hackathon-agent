"""Shared pieces for the wallet tools.

A module in this package exposes TOOLS: the agent tools for one area. To add an area, add a module and list it in
__init__.py; nobody else's file changes.

Every tool has a tier, which decides what the agent may do with it (agent/tools.py shrinks the set by risk/phase):
  read     look things up                                     (allowance, payees, offers, invoices, verified payments)
  draft    prepare something the customer must sign            (payments, purchases, refunds, new person, higher limit)
  control  protective actions that apply at once, and are logged (lower the limit, freeze payments)
No tier moves money. These modules never import app.paypal.client or the execution gateway (tests/test_architecture.py).
Optional `group`: the tool is only offered to the model when the conversation is about it (fewer tools = fewer
mistakes for a small model).
"""
from __future__ import annotations

from typing import Callable

from pydantic import BaseModel


MISHEARD_DESC = ("Only if speech recognition clearly misheard the AMOUNT: the user's words exactly as transcribed, "
                 "e.g. 'a grant' (else omit)")
MEANT_DESC = "What you're sure they meant, e.g. 'a grand'. The card shows both so the user can check."
EVIDENCE_DESC = "Quote the user's exact words that asked for this"


class NoArgs(BaseModel):
    pass


class Tool:
    def __init__(self, name: str, tier: str, description: str, args: type[BaseModel], fn: Callable,
                 api: str = "", group: str = ""):
        self.name, self.tier, self.description, self.args, self.fn = name, tier, description, args, fn
        self.api, self.group = api, group

    def schema(self) -> dict:
        params = self.args.model_json_schema()
        params.pop("title", None)
        for p in params.get("properties", {}).values():
            p.pop("title", None)
        params.setdefault("properties", {})
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": params}}


def bind(gw, tools: list[Tool]) -> list[Tool]:
    """Give each module-level handler fn(gw, s, args) access to the gateway (wallet, kernel, verifier)."""
    out = []
    for t in tools:
        fn = t.fn
        out.append(Tool(t.name, t.tier, t.description, t.args, (lambda f: lambda s, a: f(gw, s, a))(fn), t.api, t.group))
    return out
