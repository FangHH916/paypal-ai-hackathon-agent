"""Contract boundaries: real signatures, separate guardian approval, atomic frequency and honest evidence."""
import base64
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app.config import settings
from app.contract_lab import run_showcase
from app.lab import sandbox
from app.wallet.gateway import GatewayError
from app.wallet.ledger import Wallet
from app.wallet import payments
from app.kernel import p256
from app.kernel.contract import guardian_required
from app.kernel.executor import KernelError
from app.kernel.signing import build_challenge, compute_digest
from scripts.verify_receipt import check


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sb = sandbox(self.tmp.name)

    def large(self, paired=True):
        # a never-paid person: Enhanced protection also asks "is this your own idea?", answered yes below
        stranger = self.sb.wallet.add_payee("u_lee", "Tom Reed", "tom.reed@family.example", "friend")["payee_id"]
        self.gkey = self.sb.pair_guardian()[0] if paired else None
        d = self.sb.ask("Send 400 to Tom Reed", payee=stranger, cents=40000)
        d.cooling_until = time.time() - 1
        self.sb.kernel._refresh_cooling(self.sb.session, d)
        return d

    def approve(self, d):
        row = self.sb.guardian_decide(self.gkey, d)
        return self.sb.kernel.approve_guardian(self.sb.session, d.draft_id, d.digest, row["signed"])

    def test_guardian_is_required_even_after_cooling(self):
        d = self.large()
        with self.assertRaises(KernelError) as e:
            self.sb.sign(d, verify_answer=True)
        self.assertEqual(e.exception.code, "GUARDIAN_APPROVAL_REQUIRED")
        self.assertEqual(self.sb.wallet.payments, {})
        self.assertEqual(d.decision["action"], "wait_for_guardian")

    def test_no_guardian_paired_means_it_waits(self):
        d = self.large(paired=False)
        with self.assertRaises(KernelError) as e:
            self.sb.sign(d, verify_answer=True)
        self.assertEqual(e.exception.code, "GUARDIAN_APPROVAL_REQUIRED")

    def test_approved_exact_card_executes_and_persists_evidence(self):
        d = self.large()
        self.approve(d)
        r = self.sb.pay(d, self.sb.sign(d, verify_answer=True), verify_answer=True)
        record = r["verifiable"]["decision"]
        self.assertEqual(record["action"], "executed")
        self.assertEqual(record["checks"]["execution"]["status"], "pass")
        self.assertIsNotNone(record["guardian_approval"])
        self.assertTrue(all(ok for _, ok in check(r["verifiable"])), check(r["verifiable"]))
        self.assertEqual(self.sb.wallet.transaction("u_lee", d.draft_id)["decision"]["guardian_approval"], record["guardian_approval"])
        self.assertIsNone(self.sb.kernel.approvals.valid("u_lee", self.sb.session.session_id, d.draft_id, d.digest))

    def test_expired_approval_after_signing_stops_commit(self):
        d = self.large()
        self.approve(d)
        token = self.sb.kernel.authorize(self.sb.session, d.draft_id, d.digest, self.sb.sign(d, verify_answer=True), verify_answer=True)
        self.sb.kernel.approvals.records[(self.sb.session.session_id, d.draft_id)]["expires_at"] = 0
        with self.assertRaises(KernelError) as e:
            self.sb.kernel.commit(self.sb.session, d.draft_id, token)
        self.assertEqual(e.exception.code, "GUARDIAN_APPROVAL_REQUIRED")
        self.assertEqual(self.sb.balance(), 150000)

    def test_gateway_checks_approval_independently(self):
        d = self.large()
        self.approve(d)
        self.sb.kernel.authorize(self.sb.session, d.draft_id, d.digest, self.sb.sign(d, verify_answer=True), verify_answer=True)
        self.sb.kernel.approvals.revoke(self.sb.session.session_id, d.draft_id)
        with self.assertRaises(GatewayError) as e:
            self.sb.kernel.gateway.execute(user_id="u_lee", session_id=self.sb.session.session_id,
                card=d.card_fields(), digest=d.digest, auth=self.sb.session.signatures[d.draft_id],
                decision={"guardian_approval": {"approved": True}})
        self.assertEqual(e.exception.code, "GW_GUARDIAN_APPROVAL_REQUIRED")
        self.assertFalse(self.sb.wallet.payments)

    def test_gateway_reverifies_the_guardian_signature_itself(self):
        d = self.large()
        self.sb.kernel.approve_guardian(self.sb.session, d.draft_id, d.digest, {"payload": "{}", "signature": "AA=="})   # no real signature
        self.sb.kernel.authorize(self.sb.session, d.draft_id, d.digest, self.sb.sign(d, verify_answer=True), verify_answer=True)
        with self.assertRaises(GatewayError) as e:
            self.sb.kernel.gateway.execute(user_id="u_lee", session_id=self.sb.session.session_id,
                card=d.card_fields(), digest=d.digest, auth=self.sb.session.signatures[d.draft_id])
        self.assertEqual(e.exception.code, "GW_GUARDIAN_SIGNATURE")

    def test_approval_is_bound_to_customer_session_digest_and_cancel(self):
        d = self.large()
        self.approve(d)
        store = self.sb.kernel.approvals
        self.assertIsNone(store.valid("other", self.sb.session.session_id, d.draft_id, d.digest))
        self.assertIsNone(store.valid("u_lee", "other", d.draft_id, d.digest))
        self.assertIsNone(store.valid("u_lee", self.sb.session.session_id, d.draft_id, "0" * 64))
        self.sb.kernel.cancel(self.sb.session, d.draft_id)
        self.assertIsNone(store.valid("u_lee", self.sb.session.session_id, d.draft_id, d.digest))

    def test_failed_approval_audit_leaves_no_usable_approval(self):
        d = self.large()
        with patch.object(self.sb.audit, "append", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.sb.kernel.approve_guardian(self.sb.session, d.draft_id, d.digest)
        self.assertIsNone(self.sb.kernel.approvals.valid("u_lee", self.sb.session.session_id, d.draft_id, d.digest))

    def test_gateway_checks_signed_dependencies_without_kernel_decision(self):
        d = self.sb.ask("Send 50 to my daughter", cents=5000)
        d.dependency_ids = ["unexecuted"]
        d.digest = compute_digest(d)
        # Deliberately bypass the kernel but use a valid enrolled device signature.
        payload = build_challenge(self.sb.session.session_id, d)
        auth = {"payload": payload, "method": "device_key",
                "signature": base64.b64encode(p256.sign(self.sb.device_key, payload.encode())).decode()}
        with self.assertRaises(GatewayError) as e:
            self.sb.kernel.gateway.execute(user_id="u_lee", session_id=self.sb.session.session_id,
                card=d.card_fields(), digest=d.digest, auth=auth)
        self.assertEqual(e.exception.code, "GW_DEPENDENCY_NOT_COMPLETED")
        self.assertFalse(self.sb.wallet.payments)

    def test_uncompleted_dependency_blocks_signature_and_is_signed_content(self):
        self.sb.session.messages.append({"role": "user", "content": "Send 50 to my daughter", "reference_parent_id": "not-posted"})
        from app.agent.verifier import Verifier
        d = self.sb.kernel.create_draft(self.sb.session, kind="transfer", payee_id="p_daughter", amount_cents=5000,
            verification=Verifier(None, self.sb.wallet).verdict(self.sb.session))
        self.assertEqual(d.decision["checks"]["plan"]["status"], "fail")
        with self.assertRaises(KernelError) as e:
            self.sb.sign(d)
        self.assertEqual(e.exception.code, "DEPENDENCY_NOT_COMPLETED")
        d.dependency_ids = []
        self.assertNotEqual(compute_digest(d), d.digest)

    def test_velocity_is_a_hard_stop_even_with_yes_answer(self):
        self.sb.wallet.recent_exec_times["u_lee"] = [time.time()] * settings.velocity_max
        d = self.sb.ask("Send 50 to my daughter", cents=5000)
        with self.assertRaises(KernelError):
            self.sb.sign(d, verify_answer=True)
        self.assertIn("R21_VELOCITY", d.decision["reason_codes"])
        self.assertEqual(d.decision["action"], "reject")

    def _record(self, n: int) -> dict:
        digest = f"{n:x}".rjust(64, "0")
        card = {"draft_id": f"d{n}", "kind": "transfer", "currency": "USD", "amount_cents": 100, "payee_name": "Anna Lee", "plan_id": "", "payee_id": "p_daughter"}
        return payments.new_record(payment_id=f"pay_{n}", user_id="u_lee", session_id="s", card=card, digest=digest, rail="payouts",
                                   idempotency_key=f"key{n}", authorization=None, verification=None, decision=None)

    def test_atomic_last_velocity_slot_and_persistence(self):
        wallet = Wallet(self.tmp.name + "/ledger.sqlite3")
        wallet.recent_exec_times["u_lee"] = [time.time()] * (settings.velocity_max - 1)

        def attempt(i):
            try:
                return wallet.begin_payment(self._record(i), reserve=True)["state"]
            except ValueError as e:
                return str(e)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, [1, 2]))
        self.assertEqual(results.count("SUBMITTING"), 1)
        self.assertTrue(any("R21_VELOCITY" in r for r in results))
        restarted = Wallet(self.tmp.name + "/ledger.sqlite3")
        self.assertEqual(restarted.allowance_left("u_lee"), 149900)
        with self.assertRaisesRegex(ValueError, "R21_VELOCITY"):
            restarted.begin_payment(self._record(3), reserve=True)
        restarted.recent_exec_times["u_lee"] = [time.time() - settings.velocity_window_min * 60 - 1]
        self.assertEqual(restarted.begin_payment(self._record(4), reserve=True)["state"], "SUBMITTING")

    def test_evidence_does_not_claim_signature_before_signing(self):
        d = self.sb.ask("Send 50 to my daughter", cents=5000)
        self.assertEqual(d.decision["checks"]["authorization"]["status"], "pending")
        self.assertEqual(d.decision["checks"]["execution"]["status"], "pending")
        self.assertEqual(d.decision["verifier"]["mode"], "offline_shared_parser")
        self.assertEqual(self.sb.audit.for_session(self.sb.session.session_id)[-1]["event"], "contract.decision")

    def test_showcase_measures_actual_outcomes(self):
        result = run_showcase()
        self.assertEqual(result["passed"], result["total"], [(r["id"], r["code"]) for r in result["cases"] if not r["expected_behavior_met"]])
        for row in result["cases"]:
            self.assertEqual(row["paypal_sent_cents"], 5000 if row["id"] == "normal" else 0)
            self.assertEqual(row["paypal_write_calls"], 1 if row["id"] == "normal" else 0)
        self.assertEqual(result["comparison"][-1]["unsafe_candidates_admitted"], 0)
        self.assertEqual(result["comparison"][-1]["normal_candidates_admitted"], 1)


class WhoNeedsTheGuardian(unittest.TestCase):
    """Family and people paid before: no guardian. Someone new above the threshold, a flagged merchant, scam signs: guardian."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sb = sandbox(self.tmp.name)

    def card(self, payee_id, cents, kind="transfer", **kw):
        return {"user_id": "u_lee", "payee_id": payee_id, "amount_cents": cents, "kind": kind, **kw}

    def test_rules(self):
        new = self.sb.wallet.add_payee("u_lee", "Tom Reed", "tom.reed@mail.example", "friend")["payee_id"]
        self.assertFalse(guardian_required(self.card("p_daughter", 90000), self.sb.wallet))   # family, paid before
        self.assertFalse(guardian_required(self.card("p_power", 90000, "bill"), self.sb.wallet))   # a bill from a known biller
        self.assertFalse(guardian_required(self.card(new, 20000), self.sb.wallet))            # new, small
        self.assertTrue(guardian_required(self.card(new, 40000), self.sb.wallet))             # new, above the threshold
        self.assertTrue(guardian_required(self.card("p_daughter", 100, scam_level="MEDIUM"), self.sb.wallet))
        self.assertTrue(guardian_required(self.card("m", 100, "purchase", trust={"guardian": True}), self.sb.wallet))
        self.assertFalse(guardian_required(self.card("p_son", 90000, "refund", scam_level="MEDIUM"), self.sb.wallet))
        self.assertFalse(guardian_required(self.card("p_son", 90000, "money_request"), self.sb.wallet))
