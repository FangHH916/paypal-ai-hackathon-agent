"""Core data structures. Amounts are always stored as integer cents to avoid floating-point errors."""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.config import settings


def to_cents(amount: float | str) -> int:
    return int(round(float(amount) * 100))


def fmt_money(cents: int, currency: str | None = None) -> str:
    """The one place money becomes text. Dollar currencies read "$12.00"; anything else "EUR 12.00"."""
    currency = currency or settings.wallet_currency
    return f"${cents / 100:,.2f}" if currency == "USD" else f"{currency} {cents / 100:,.2f}"


class RiskLevel(str, Enum):
    LOW = "LOW"          # one biometric check
    MEDIUM = "MEDIUM"    # biometric + one verification question
    HIGH = "HIGH"        # cooling-off; risky payments additionally need the family guardian
    BLOCK = "BLOCK"      # not allowed to execute

    @property
    def rank(self) -> int:
        return ["LOW", "MEDIUM", "HIGH", "BLOCK"].index(self.value)

    @staticmethod
    def max(*levels: "RiskLevel") -> "RiskLevel":
        return max(levels, key=lambda l: l.rank)


class Phase(str, Enum):
    """Phases of the session state machine: can only advance along allowed paths."""
    IDLE = "IDLE"
    DRAFTED = "DRAFTED"            # draft created and graded by the rules
    COOLING = "COOLING"            # high-risk cooling-off period
    AWAITING_AUTH = "AWAITING_AUTH"
    EXECUTING = "EXECUTING"        # signed and handed to the gateway: too late to cancel, whatever PayPal says next
    EXECUTED = "EXECUTED"          # the gateway accepted it; the payment's own lifecycle lives on its record
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    FROZEN = "FROZEN"              # the independent verifier disagreed with the draft: it can never be signed
    HANDOFF = "HANDOFF"            # handed off to a human


class RiskDecision(BaseModel):
    level: RiskLevel
    reasons: list[str] = Field(default_factory=list)       # plain-language reasons shown to the user
    rule_hits: list[str] = Field(default_factory=list)     # rule codes for the audit log
    verify_question: Optional[str] = None
    verify_kind: Optional[str] = None                      # "new_payee": a "no" stops the payment as a likely scam
    cooldown_seconds: int = 0
    blocking: list[str] = Field(default_factory=list)      # the reasons that stop it outright (BLOCK rules only)


class Draft(BaseModel):
    """Transaction draft: the furthest the Agent can go. Execution only accepts this draft + the user's signature."""
    draft_id: str
    user_id: str
    kind: Literal["transfer", "bill", "purchase", "refund", "money_request"]
    rail: str = ""                   # payouts | orders | invoice | refund | payout_cancel | money_request
    from_account: str = "WALLET"     # always the single wallet; kept on the card so what is signed stays explicit
    payee_id: str                    # a saved person or biller; for a purchase the merchant id
    payee_name: str
    payee_handle: str = ""           # the PayPal email money would go to, if any (signed, masked in the audit log)
    amount_cents: int
    currency: str = Field(default_factory=lambda: settings.wallet_currency)
    note: str = ""
    invoice_id: Optional[str] = None
    invoice_number: Optional[str] = None
    merchant_id: Optional[str] = None
    merchant_name: Optional[str] = None
    items: list[dict] = Field(default_factory=list)  # [{offer_id, name, quantity, unit_cents}]: from the catalogue
    capture_id: Optional[str] = None
    parent_payment_id: Optional[str] = None
    trust: dict = Field(default_factory=dict)        # deterministic merchant / invoice checks (kernel/trust.py)
    scam_level: str = "LOW"          # the session's scam level when this was drafted (signed: it decides guardian rules)
    created_at: float
    expires_at: float
    nonce: str
    digest: str = ""                 # hash of the confirmation card content: "what you see is what you sign"
    risk: Optional[RiskDecision] = None
    cooling_until: float = 0.0
    taint: list[str] = Field(default_factory=list)  # fields that came from untrusted sources
    grounding: dict = Field(default_factory=dict)  # is payee/amount backed by the user's own words (kernel/evidence.py)
    you_said: str = ""                             # the user's own words this payment is based on (shown + signed)
    verification: dict = Field(default_factory=dict)  # independent verifier's verdict (kernel/crosscheck.py)
    dependency_ids: list[str] = Field(default_factory=list)
    you_said_audio: list[str] = Field(default_factory=list)  # SHA-256 of the recordings you_said came from (signed)
    plan_id: str = ""                 # steps of ONE request the user said in one go share this id (signed with the card)
    decision: dict = Field(default_factory=dict)  # server-side evidence, not part of the customer's signed card

    def card_fields(self) -> dict:
        """Fields that go into the hash: every item shown on the confirmation card is here."""
        return {
            "draft_id": self.draft_id, "user_id": self.user_id, "kind": self.kind, "rail": self.rail,
            "from_account": self.from_account, "payee_id": self.payee_id,
            "payee_name": self.payee_name, "payee_handle": self.payee_handle, "amount_cents": self.amount_cents,
            "currency": self.currency, "invoice_id": self.invoice_id, "invoice_number": self.invoice_number,
            "merchant_id": self.merchant_id, "merchant_name": self.merchant_name, "items": self.items,
            "capture_id": self.capture_id, "parent_payment_id": self.parent_payment_id, "trust": self.trust,
            "scam_level": self.scam_level, "you_said": self.you_said, "you_said_audio": self.you_said_audio,
            "dependency_ids": self.dependency_ids, "plan_id": self.plan_id,
            "nonce": self.nonce, "expires_at": int(self.expires_at),
        }
