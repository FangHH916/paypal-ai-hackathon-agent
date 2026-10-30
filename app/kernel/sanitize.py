"""Text that came from outside (an invoice note, a merchant listing) is data, never instructions.

Used in two places: the tool gateway cleans every tool result before the model sees it, and kernel/trust.py treats "this
invoice tried to instruct the assistant" as a stop in itself (rule R84).
"""
from __future__ import annotations

import re
from typing import Any

INJECTION = re.compile(r"(ignore|disregard|forget) (all |any )?(the )?(previous|prior|above|earlier) "
                       r"(instructions|rules|prompts?)|system prompt|you are now|admin mode|developer mode|"
                       r"(note|message|instruction)s? (to|for) (the )?(assistant|ai|model|agent)|system (note|message)|"
                       r"\b(call|use|invoke) (the )?(tool |function )?(draft_\w+|handoff_to_human)|"
                       r"\b(assistant|ai agent|ai model|agent)\s*[:,]|\bapprove (and pay|the payment|this (invoice|payment))\b|"
                       r"do not (warn|tell|mention)|payee_id|tool[_ ]call", re.I)
_SENTENCES = re.compile(r"[^.!?\n]+[.!?]*")
REMOVED = "[suspicious instruction removed]"


def has_instruction(text: str) -> bool:
    return bool(INJECTION.search(text or ""))


def sanitize(obj: Any) -> Any:
    """post-hook: if a text field in a tool result smuggles in instructions, drop the WHOLE sentence that carries
    them (not just the matching words, which would leave half an instruction behind) and flag it."""
    if isinstance(obj, str):
        if not INJECTION.search(obj):
            return obj
        kept = [x.strip() for x in _SENTENCES.findall(obj) if x.strip() and not INJECTION.search(x)]
        return " ".join(kept + [REMOVED])
    if isinstance(obj, list):
        return [sanitize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    return obj
