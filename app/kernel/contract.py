"""Deterministic transaction contract. Evidence is produced by the same gates that authorize payment.

Decision records are server observations, NOT a customer's signature over the risk assessment.
The second person here is the family guardian: a separate device, a separate key, the same exact card.
"""
from __future__ import annotations

import copy
import threading
import time

from app.config import settings

POLICY_VERSION = "star-contract-2.1"
NEW_PAYEE_DAYS = 30           # how long someone she added herself still counts as new


def payee_ramp(wallet, user_id, payee_id, amount_cents, draft_id="") -> bool:
    """The small-test-payment scam: a little first, so the big one no longer looks like a first payment. For someone added
    to her list (never the household people the demo starts with), history doesn't buy trust while the amounts still add up
    past the threshold, and nothing above the threshold is waved through for being more than she has ever sent them."""
    payee = wallet.payee(user_id, payee_id) or {}
    added = payee.get("added_at")
    if payee.get("type") != "person" or not added:
        return False
    limit = settings.guardian_threshold * 100
    if amount_cents > limit and amount_cents > max(wallet.payee_history(user_id, payee_id), default=0):
        return True
    return time.time() - added <= NEW_PAYEE_DAYS * 86400 \
        and wallet.payee_sent_total(user_id, payee_id, exclude_draft=draft_id) + amount_cents > limit


def guardian_required(card, wallet):
    """When must the family co-approve? Money to someone she has never paid before above GUARDIAN_THRESHOLD (impersonation
    scams always use a new payee), the same person building up to it with small payments (payee_ramp), a merchant or invoice
    check marked for it (kernel/trust.py), and any payment in a conversation that showed scam signs but was not stopped.
    Not: refunds, or asking someone to pay her back."""
    if card["kind"] in ("refund", "money_request"):
        return False
    if (card.get("trust") or {}).get("guardian"):
        return True
    if card.get("scam_level") in ("MEDIUM", "HIGH"):
        return True
    if card["kind"] != "transfer":
        return False
    user = card["user_id"]
    payee = wallet.payee(user, card["payee_id"]) or {}
    if payee.get("type") != "person":
        return False
    if payee_ramp(wallet, user, card["payee_id"], card["amount_cents"], card.get("draft_id", "")):
        return True
    return not wallet.payee_history(user, card["payee_id"]) and card["amount_cents"] > settings.guardian_threshold * 100


class GuardianApprovals:
    """Server-owned, digest-bound approvals, each carrying the guardian device's own signature. Lost on restart, like
    pending drafts: fail closed. Approval can never unlock a BLOCK or FROZEN draft (the kernel refuses to record one, and
    those gates run before and regardless of this one) and a guardian can't start a payment (I9)."""
    TTL = 300

    def __init__(self):
        self.records = {}
        self.lock = threading.RLock()

    def approve(self, user_id, session_id, draft_id, digest, expires_at, signed: dict | None = None):
        with self.lock:
            self.records = {key: value for key, value in self.records.items() if value["expires_at"] > time.time()}
            rec = {"approval_id": "gapp_" + digest[:12], "user_id": user_id, "session_id": session_id, "draft_id": draft_id,
                   "digest": digest, "decision": "approve", "expires_at": min(expires_at, time.time() + self.TTL),
                   "approved_at": time.time(), "policy_version": POLICY_VERSION, "method": "family guardian device",
                   "signed": signed or {}}
            self.records[(session_id, draft_id)] = rec
            return copy.deepcopy(rec)

    def deny(self, user_id, session_id, draft_id, digest, signed: dict | None = None):
        with self.lock:
            rec = {"approval_id": "gden_" + digest[:12], "user_id": user_id, "session_id": session_id, "draft_id": draft_id,
                   "digest": digest, "decision": "deny", "expires_at": time.time() + self.TTL * 12,
                   "denied_at": time.time(), "policy_version": POLICY_VERSION, "signed": signed or {}}
            self.records[(session_id, draft_id)] = rec
            return copy.deepcopy(rec)

    def valid(self, user_id, session_id, draft_id, digest):
        with self.lock:
            rec = self.records.get((session_id, draft_id), {})
            return copy.deepcopy(rec) if (rec.get("decision") == "approve" and rec.get("user_id") == user_id
                and rec.get("digest") == digest and rec.get("expires_at", 0) > time.time()
                and rec.get("policy_version") == POLICY_VERSION) else None

    def denied(self, session_id, draft_id):
        with self.lock:
            return self.records.get((session_id, draft_id), {}).get("decision") == "deny"

    def revoke(self, session_id, draft_id):
        with self.lock:
            self.records.pop((session_id, draft_id), None)


def decision(draft, session, wallet, approvals, *, stage="before_authorization", signature=False,
             verify_answer=False, execution=False, rejection=None):
    """Build a fail-closed decision. Only explicit observations produce a pass."""
    check = lambda status, detail: {"status": status, "detail": detail}
    v = draft.verification
    checks = {}
    checks["intent"] = check("pass" if v.get("status") == "match" else
        "fail" if v.get("status") == "mismatch" else "pending",
        " ".join(v.get("mismatches", [])) or "Verifier: " + v.get("engine", "not available"))
    missing = [ref for ref in draft.dependency_ids if not
        ((wallet.transaction(draft.user_id, ref) or {}).get("status") == "SUCCESS")]
    checks["plan"] = check("fail" if missing else "pass" if draft.dependency_ids else "not_applicable",
        "Uncompleted dependencies: " + ", ".join(missing) if missing else
        "Verified earlier steps: " + ", ".join(draft.dependency_ids) if draft.dependency_ids else "No prior transaction is required.")
    risk = draft.risk
    blocked = not risk or risk.level.value == "BLOCK"
    cooling = draft.cooling_until > time.time()
    question = bool(risk and risk.verify_question and not verify_answer)
    checks["policy"] = check("fail" if blocked else "pending" if cooling or question else "pass",
        " ".join(risk.reasons) if risk and risk.reasons else "Transaction rules passed." if risk else "Rules have not run.")
    required = guardian_required(draft.card_fields(), wallet)
    approval = approvals.valid(draft.user_id, session.session_id, draft.draft_id, draft.digest)
    denied = approvals.denied(session.session_id, draft.draft_id)
    checks["guardian_approval"] = check(
        "fail" if denied else "pass" if required and approval else "pending" if required else "not_applicable",
        "Your family said no to this exact card." if denied else
        "Your family guardian approved this exact card on their own device." if required and approval else
        "Your family guardian must approve this exact card." if required else "Not required by this policy.")
    checks["authorization"] = check("pass" if signature else "pending", "Device signature verified." if signature else "Waiting for the customer's signature.")
    checks["execution"] = check("pass" if execution else "fail" if rejection else "pending",
        "Gateway verified; PayPal call made." if execution else rejection or "No execution has been confirmed.")
    if rejection:
        action = "reject"
    elif v.get("status") == "mismatch":
        action = "freeze"
    elif missing or blocked or denied:
        action = "reject"
    elif v.get("status") != "match":
        action = "wait_for_validation"
    elif cooling:
        action = "wait"
    elif required and not approval:
        action = "wait_for_guardian"
    elif question:
        action = "clarify"
    else:
        action = "executed" if execution else "execute" if signature else "request_signature"
    required_evidence = [k for k, c in checks.items() if c["status"] == "pending" and k != "execution"]
    return {"policy_version": POLICY_VERSION, "transaction_id": draft.draft_id,
            "session_id": session.session_id, "digest": draft.digest, "stage": stage,
            "action": action, "checks": checks, "reason_codes": list(risk.rule_hits) if risk else [],
            "required_evidence": required_evidence, "recorded_at": time.time(),
            "guardian_approval": {k: approval[k] for k in ("approval_id", "digest", "approved_at", "method")} if required and approval else None,
            "dependency_ids": list(draft.dependency_ids),
            "raw_text": "\n".join(m.get("raw_content") or m["content"] for m in session.messages[session.task_start:]
                                     if m.get("role") == "user") or draft.you_said,
            "draft": {"payee": draft.payee_name, "amount_cents": draft.amount_cents, "kind": draft.kind, "rail": draft.rail},
            "verifier": {"engine": v.get("engine", "unavailable"), "extracted": v.get("extracted"),
                         "mode": "offline_shared_parser" if "offline" in v.get("engine", "").lower() else "model_or_test_fixture"},
            "evidence_scope": "Server observations; the customer signs the transaction card, not this report."}
