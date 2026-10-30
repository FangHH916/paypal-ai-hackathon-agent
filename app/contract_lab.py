"""Reproducible fault injection, isolated from customer sessions and all online models.

The comparison is a counterfactual replay of observed gate results, not a weakened production system,
not human-subject research, and not a live-model accuracy benchmark.
"""
from __future__ import annotations

import tempfile
import time

from app.kernel.contract import POLICY_VERSION
from app.kernel.executor import KernelError
from app.lab import USER, sandbox
from app.wallet.ledger import WALLET_ACCOUNT

CASES = (
    ("normal", "Normal transfer", "All four contract principles", "Send 50 to my daughter", 5000),
    ("amount", "AI changes 50 to 500", "Faithful intent", "Send 50 to my daughter", 50000),
    ("payee", "AI selects the wrong recipient", "Faithful intent", "Send 50 to my daughter", 5000),
    ("dependency", "Earlier step never completed", "Valid plan dependencies", "Send 50 to my daughter", 5000),
    ("velocity", "Payment window is full", "Safe execution conditions", "Send 50 to my daughter", 5000),
    ("tamper", "Card changes after signing", "Exact authorization", "Send 50 to my daughter", 5000),
    ("allowance", "Allowance used up after signing", "Safe execution conditions", "Send 50 to my daughter", 5000),
    ("guardian", "Large payment to a new person, no family approval", "Separate approval", "Send 400 to Tom Reed", 40000),
)
EXPECTED_CODES = {"normal": "", "amount": "NOT_AWAITING_AUTH", "payee": "NOT_AWAITING_AUTH",
                  "dependency": "DEPENDENCY_NOT_COMPLETED", "velocity": "NOT_AWAITING_AUTH",
                  "tamper": "DIGEST_MISMATCH", "allowance": "RECHECK_BLOCKED", "guardian": "GUARDIAN_APPROVAL_REQUIRED"}


def run_showcase():
    rows = []
    with tempfile.TemporaryDirectory(prefix="star-contract-") as tmp:
        for ident, title, principle, text, cents in CASES:
            sb = sandbox(tmp)
            before = sb.balance()
            start = time.perf_counter()
            if ident == "velocity":
                from app.config import settings
                sb.wallet.recent_exec_times[USER] = [time.time()] * settings.velocity_max
            sb.session.messages.append({"role": "user", "content": text,
                **({"reference_parent_id": "unexecuted-parent"} if ident == "dependency" else {})})
            from app.agent.verifier import Verifier
            payee_id = "p_son" if ident == "payee" else "p_daughter"
            if ident == "guardian":   # someone the customer has never paid: the case that needs a second person
                payee_id = sb.wallet.add_payee(USER, "Tom Reed", "tom.reed@mail.example", "friend")["payee_id"]
            d = sb.kernel.create_draft(sb.session, kind="transfer", payee_id=payee_id,
                amount_cents=cents, verification=Verifier(None, sb.wallet).verdict(sb.session))
            created = d.decision
            code, receipt = "", None
            try:
                if ident == "guardian":
                    # Fixture clock only: isolate the missing approval from the separate cooling gate.
                    d.cooling_until = time.time() - 1
                    sb.kernel._refresh_cooling(sb.session, d)
                assertion = sb.sign(d, verify_answer=True)
                if ident == "tamper":
                    d.amount_cents += 100
                if ident == "allowance":
                    sb.wallet.account(USER, WALLET_ACCOUNT)["balance_cents"] = 1
                receipt = sb.pay(d, assertion, verify_answer=True)
            except KernelError as e:
                code = e.code
                sb.kernel.record_decision(sb.session, d, stage="rejected", rejection=code)
            sent = sum(r["amount_cents"] for r in sb.wallet.payments.values())
            expected = code == EXPECTED_CODES[ident] and sent == (5000 if ident == "normal" else 0)
            if ident in ("amount", "payee"):
                expected = expected and created["checks"]["intent"]["status"] == "fail"
            if ident == "velocity":
                expected = expected and "R21_VELOCITY" in created["reason_codes"]
            rows.append({"id": ident, "title": title, "principle": principle, "input": text,
                "fault_injected": ident != "normal", "expected_behavior_met": expected,
                "result": "executed" if receipt else "rejected", "code": code or "COMMITTED",
                "paypal_sent_cents": sent, "paypal_write_calls": len(sb.writes()), "allowance_before_cents": before,
                "allowance_after_cents": sb.balance(), "external_allowance_change": ident == "allowance",
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                "decision_at_draft": created, "decision": d.decision,
                "audit_integrity": sb.audit.verify(), "audit": sb.audit.for_session(sb.session.session_id)})

    # Compare only pre-authorization gates: same recorded candidates, no fake signatures or real calls.
    selected = [r for r in rows if r["id"] in ("normal", "amount", "payee", "dependency", "velocity")]
    comparison = []
    for label, gates in [("A · Signature prerequisites only", []),
                         ("B · + transaction policy", ["policy"]),
                         ("C · + intent and dependencies", ["policy", "intent", "plan"])]:
        outcomes = []
        for row in selected:
            checks = row["decision_at_draft"]["checks"]
            # A yes/no confirmation is not a hard rejection: assume the customer would answer yes.
            passes = all(checks[k]["status"] != "fail" if k == "policy" else
                         checks[k]["status"] in ("pass", "not_applicable") for k in gates)
            outcomes.append({"id": row["id"], "would_reach_authorization": passes})
        comparison.append({"label": label, "gates": gates, "outcomes": outcomes,
            "unsafe_candidates_admitted": sum(x["would_reach_authorization"] for x in outcomes if x["id"] != "normal"),
            "unsafe_candidates": len(selected) - 1,
            "normal_candidates_admitted": sum(x["would_reach_authorization"] for x in outcomes if x["id"] == "normal"),
            "normal_candidates": 1})
    return {"policy_version": POLICY_VERSION, "generated_at": time.time(), "cases": rows,
        "passed": sum(r["expected_behavior_met"] for r in rows), "total": len(rows),
        "comparison": comparison,
        "methodology": "Eight isolated deterministic scenarios use the real kernel, device signatures and gateway, on a simulator. "
            "The five-candidate comparison replays recorded pre-authorization gates (confirmation answers assumed yes); it does not execute a weakened system. "
            "The verifier is an offline shared parser. No live-model accuracy, user friction or population failure rate is measured. "
            "Faults and the guardian-case fixture clock are injected only in fresh sandboxes."}
