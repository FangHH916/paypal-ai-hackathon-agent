"""Zero-trust execution gateway: the ONLY way a signed payment reaches PayPal.

It sits in front of the money-moving client and trusts nobody upstream - not the agent (which can't reach it at all),
and not even the kernel. Every instruction must carry the customer's own device signature, and the gateway checks it
again itself before a single request goes out:

  1. the card it is asked to execute hashes to the digest that was signed      (what was shown = what is executed)
  2. the signed payload is a payment challenge for exactly this card            (payee, amount, rail, merchant, session)
  3. the signature verifies against a credential ENROLLED for this customer    (looked up here, not passed in)
     - a passkey (device biometric, UV flag required) or, only if the customer has no passkey, a browser key
  4. the signed payload has not been used before                                (its own replay list)
  5. the signature is fresh                                                     (challenge expiry + small grace)
  6. when the family must co-approve, the guardian's own signature covers this digest

Then: write-ahead record saved (I3), idempotency keys derived from the digest (I4), and only then the HTTP call, made
outside every lock. Anything missing -> rejected with a code, logged, nothing sent. A conversation request with no
signature from the customer's device can never execute, however it reaches this point.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import threading
import time
from urllib.parse import urlparse, urlsplit

from app.config import settings
from app.kernel.contract import GuardianApprovals, guardian_required
from app.kernel.signing import CHALLENGE_DOMAIN, SignatureError, Site, canonical, check_signature
from app.paypal import calls, factory, ids
from app.paypal.config import APPROVAL_HOST_SUFFIX, SIMULATOR_BASE
from app.paypal.errors import PayPalConfigError, PayPalError, PayPalUnknown
from app.paypal.reconcile import Reconciler
from app.paypal.webhooks import WebhookHandler
from app.wallet import payments
from app.wallet.guardian import GuardianDesk
from app.wallet.invoices import InvoiceDesk

GRACE_SECONDS = 60


class GatewayError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class ExecutionGateway:
    def __init__(self, wallet, registry, audit=None, allow_unsigned: bool = False, paypal: factory.PayPal | None = None):
        self._wallet, self.registry, self.audit = wallet, registry, audit
        self.allow_unsigned = allow_unsigned          # only for quick scripts in AUTH_MODE=mock; never in the demo
        self.paypal = paypal or factory.build(audit=audit)   # no argument: an isolated in-memory simulator (tests, the lab)
        self.client, self.reader, self.simulator = self.paypal.client, self.paypal.reader, self.paypal.simulator
        self.calls = self.paypal.log
        self.approvals = GuardianApprovals()
        self.guardian = GuardianDesk(wallet, audit)
        self.invoices = InvoiceDesk(wallet, self.reader, self.simulator, audit)
        self.reconciler = Reconciler(wallet, self.client, audit, self.simulator)
        self.webhooks = WebhookHandler(wallet, self.client, audit, self.reconciler)
        self.reconciler.webhooks = self.webhooks
        self._consumed: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ what the rest of the app may ask
    def rail_error(self) -> str:
        return self.paypal.cfg.error

    def mode_status(self) -> dict:
        return self.paypal.cfg.status()

    def receiver_for(self, payee: dict) -> str:
        """The PayPal account behind a saved payee: the sandbox mapping if set, otherwise what is on the payee."""
        return (self.paypal.cfg.receivers or {}).get(payee.get("payee_id", ""), "") or payee.get("paypal_email", "")

    def next_step(self, rec: dict) -> dict | None:
        """Where the browser goes next. A redirect target is checked against an allowlist first: fail closed."""
        url = rec["paypal"].get("approve_url", "")
        if rec["state"] in ("AWAITING_APPROVAL", "AWAITING_PAYMENT") and url:
            safe = self._safe_redirect(url)
            if safe:
                return {"type": "redirect", "url": safe}
        return None if rec["state"] in payments.TERMINAL else {"type": "track"}

    def _safe_redirect(self, url: str) -> str:
        if self.simulator:
            return url[len(SIMULATOR_BASE):] if url.startswith(SIMULATOR_BASE + "/simulator/") else ""
        parts = urlsplit(url)
        return url if parts.scheme == "https" and (parts.hostname or "").endswith(APPROVAL_HOST_SUFFIX) else ""

    def receipt(self, rec: dict, replay: bool = False) -> dict:
        body = payments.receipt_body(rec, self.paypal.cfg.mode)
        pp = rec["paypal"]
        return {"txn_id": rec["payment_id"], "payment_id": rec["payment_id"], "status": body["status"], "state": rec["state"],
                "verified": rec["verified"], "verified_by": rec["verified_by"], "amount_cents": rec["amount_cents"],
                "payee_id": rec["card"].get("payee_id"), "kind": rec["kind"], "rail": rec["rail"], "executed_at": rec["created_at"],
                "user_id": rec["user_id"], "draft_id": rec["draft_id"], "paypal_mode": self.paypal.cfg.label,
                "balance_after_cents": self._wallet.allowance_left(rec["user_id"]),
                "paypal": {k: v for k, v in pp.items() if v and k not in ("debug_ids", "stage", "return_base")},
                "next_step": self.next_step(rec), "verifiable": body, **({"idempotent_replay": True} if replay else {})}

    # ------------------------------------------------------------------ execute
    def execute(self, *, user_id: str, session_id: str, card: dict, digest: str, auth: dict | None,
                verification: dict | None = None, decision: dict | None = None, return_base: str = "") -> dict:
        try:
            self._check(user_id, session_id, card, digest, auth)
            approval = self.approvals.valid(user_id, session_id, card["draft_id"], digest)
            if guardian_required(card, self._wallet):
                if not approval:
                    raise GatewayError("Your family guardian's approval is missing or expired.", "GW_GUARDIAN_APPROVAL_REQUIRED")
                if not self.guardian.verify_signed(user_id, card["draft_id"], digest, approval.get("signed") or {}, "approve"):
                    raise GatewayError("The family approval isn't signed by the paired guardian.", "GW_GUARDIAN_SIGNATURE")
            for ref in card.get("dependency_ids", []):
                if (self._wallet.transaction(user_id, ref) or {}).get("status") != "SUCCESS":
                    raise GatewayError("A required earlier payment is not complete.", "GW_DEPENDENCY_NOT_COMPLETED")
            if self.rail_error():
                raise GatewayError(self.rail_error(), "GW_RAIL_UNAVAILABLE")
        except GatewayError as e:
            self._log("gateway.rejected", session_id, {"draft_id": card.get("draft_id"), "reason": e.code})
            raise
        rail = card["rail"]
        key = ids.key_for(rail, digest)
        existing = self._wallet.payment_by_key(key)
        if existing:                                  # a double click reaches the same record, never a second payment
            return self.receipt(existing, replay=True)
        payment_id = "pay_" + digest[:16]
        recorded = copy.deepcopy(decision) if decision else None
        if recorded:
            recorded.update(stage="executed", action="executed", required_evidence=[], recorded_at=time.time())
            recorded["checks"]["authorization"] = {"status": "pass" if auth else "not_applicable",
                "detail": "Gateway independently verified the device signature." if auth else "Unsigned test fixture."}
            recorded["checks"]["execution"] = {"status": "pass", "detail": "Gateway verified; the write-ahead record is saved before PayPal is called."}
            if approval:
                recorded["guardian_approval"] = {k: approval[k] for k in ("approval_id", "digest", "approved_at", "method")}
        rec = payments.new_record(payment_id=payment_id, user_id=user_id, session_id=session_id, card=card, digest=digest,
                                  rail=rail, idempotency_key=key, authorization=auth, verification=verification, decision=recorded)
        rec["paypal"]["return_base"] = return_base or "http://localhost:8000"
        try:
            rec = self._wallet.begin_payment(rec, reserve=rec["kind"] in payments.SPENDS)    # write-ahead (I3)
        except (ValueError, OSError) as e:
            raise GatewayError(str(e), "GW_LEDGER_REJECTED") from e
        self._flush()
        with calls.scope(user_id, session_id, payment_id):
            try:
                self.reconciler.submit(rec)
            except PayPalConfigError as e:           # can't happen after rail_error(), but never count it as sent
                self._wallet.update_payment(payment_id, "FAILED", source="gateway", note="PayPal not configured")
                self._flush()
                raise GatewayError(str(e), "GW_RAIL_UNAVAILABLE") from e
            except PayPalError as e:
                # A definite refusal: nothing happened. The allowance comes back and she is told.
                self._wallet.update_payment(payment_id, "FAILED", source="paypal", note=f"{e.status} {e.name}"[:120],
                                            paypal={"debug_ids": rec["paypal"]["debug_ids"] + ([e.debug_id] if e.debug_id else [])})
                self._flush()
                self._log("gateway.rejected", session_id, {"draft_id": card["draft_id"], "reason": "GW_PAYPAL_REJECTED",
                                                           "status": e.status, "name": e.name, "debug_id": e.debug_id})
                raise GatewayError("PayPal said no, so nothing was sent.", "GW_PAYPAL_REJECTED") from e
            except PayPalUnknown as e:
                # Not an error to her: we don't know yet. Same key, never a new one (I6).
                self.reconciler.mark_unknown(rec, e.reason)
        self.guardian.clear_request(user_id, card["draft_id"])
        self._flush()
        return self.receipt(rec)

    # ------------------------------------------------------------------ checks
    def _check(self, user_id: str, session_id: str, card: dict, digest: str, auth: dict | None) -> str:
        if hashlib.sha256(canonical(card).encode()).hexdigest() != digest or card.get("user_id") != user_id:
            raise GatewayError("The instruction doesn't match the signed card.", "GW_DIGEST_MISMATCH")
        if not auth or "payload" not in auth:
            if self.allow_unsigned:
                return "unsigned (mock mode)"
            raise GatewayError("No signature from the customer's device. Refused.", "GW_UNSIGNED")
        try:
            signed = json.loads(auth["payload"])
        except (TypeError, ValueError) as e:
            raise GatewayError("Malformed signed payload.", "GW_MALFORMED") from e
        expected = {"domain": CHALLENGE_DOMAIN, "session_id": session_id, "draft_id": card["draft_id"],
                    "digest": digest, "kind": card["kind"], "rail": card["rail"], "payee": card["payee_name"],
                    "merchant_id": card["merchant_id"], "amount_cents": card["amount_cents"], "currency": card["currency"],
                    "from_account": card["from_account"]}
        if any(signed.get(k) != v for k, v in expected.items()):
            raise GatewayError("The signature is for a different payment.", "GW_PAYLOAD_MISMATCH")
        if signed.get("expires_at", 0) + GRACE_SECONDS < time.time():
            raise GatewayError("The signature is too old.", "GW_EXPIRED")
        try:
            rec = check_signature(self.registry, user_id=user_id, session_id=session_id, payload=auth["payload"],
                                  assertion={**auth, "type": "webauthn" if auth.get("method") == "passkey" else None},
                                  site=self._site(auth), check_counter=False)
        except SignatureError as e:
            raise GatewayError(f"Signature check failed at the gateway: {e}", "GW_" + e.code) from e
        key = hashlib.sha256(auth["payload"].encode()).hexdigest()
        with self._lock:
            if key in self._consumed:
                raise GatewayError("This signature was already used.", "GW_REPLAY")
            self._consumed.add(key)
        return rec["method"]

    @staticmethod
    def _site(auth: dict) -> Site:
        """Passkeys: the rp id must be one of our own hosts and the origin must be that same host."""
        if auth.get("method") != "passkey":
            return Site()
        rp_id = auth.get("rp_id", "")
        if rp_id not in settings.allowed_hosts:
            raise GatewayError("Passkey for an unknown site.", "GW_RP_MISMATCH")
        try:
            cd = json.loads(base64.urlsafe_b64decode(auth["client_data_json"] + "=" * (-len(auth["client_data_json"]) % 4)))
            origin = cd.get("origin", "")
        except Exception as e:  # noqa: BLE001
            raise GatewayError("Malformed passkey data.", "GW_MALFORMED") from e
        if urlparse(origin).hostname != rp_id.strip("[]"):
            raise GatewayError("Passkey used from another website.", "GW_ORIGIN_MISMATCH")
        return Site(rp_id, frozenset({origin}))

    def _flush(self):
        if self.audit:
            self._wallet.flush_audit(self.audit)

    def _log(self, event: str, session_id: str, data: dict):
        if self.audit:
            self.audit.append(event, session_id, data)
