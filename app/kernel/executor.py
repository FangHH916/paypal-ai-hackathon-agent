"""Public entry point of the deterministic kernel: draft, authorize, commit, cancel.

Out of the model's reach: the Agent's tool gateway can only call create_draft (a draft, no side effects).
authorize / commit can only be called by the frontend over HTTP after the user's own device has signed. Even then,
commit does not call PayPal itself: it hands the signed instruction to the execution gateway
(app/wallet/gateway.py), which verifies the signature again on its own before the first request goes out.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid

from app.config import settings
from app.kernel import changes, contract, crosscheck, p256, rules, trust, webauthn
from app.kernel import protection as prot
from app.kernel.audit import AuditLog
from app.kernel.credentials import CredentialRegistry
from app.kernel.evidence import confirmed_proposal, ground, user_utterances
from app.kernel.signing import (BIOMETRIC_REQUIRED, DEFAULT_SITE, MockAuthenticator, SignatureError, Site, TokenService,
                                browser_key_allowed, build_challenge, build_settings_challenge, canonical,
                                check_signature, compute_digest)
from app.kernel.state import Session
from app.models import Draft, Phase, RiskLevel, fmt_money
from app.wallet import catalogue, payments
from app.wallet.gateway import ExecutionGateway, GatewayError
from app.wallet.ledger import WALLET_ACCOUNT, Wallet

_RAIL = {"transfer": "payouts", "bill": "invoice", "purchase": "orders", "money_request": "money_request"}
# The rule that stopped something, in words for the Activity grid
_STOP_NAMES = {"R03_OVER_ALLOWANCE": "Over the allowance", "R04_HARD_LIMIT": "Over Star's limit", "R09_RAIL_UNAVAILABLE": "PayPal not configured",
               "R13_DAILY_LIMIT": "Over the daily limit", "R14_WALLET_FROZEN": "Payments frozen", "R21_VELOCITY": "Too many payments",
               "R52_NOT_REQUESTED": "Nobody asked for this", "R53_WRONG_SHARE": "Wrong share of an amount",
               "R60_UNKNOWN_OUTCOME": "Earlier payment still unknown", "R62_BILL_IN_PROGRESS": "Bill already being paid", "R80_INVOICE_PHONE": "Phone number in the invoice",
               "R84_INSTRUCTION_IN_DATA": "Hidden instruction in the text", "R91_PRICE_ANOMALY": "Price far too high", "R92_MERCHANT_FLAGGED": "Flagged seller",
               "R93_NOT_IN_CATALOGUE": "Not the catalogue's offer", "R94_GIFT_CARD": "Gift card scam signs", "R95_REFUND_INVALID": "Refund not possible",
               "R96_REFUND_SCAM": "Refund scam signs", "R00_UNKNOWN_PAYEE": "Unknown person"}


def stop_name(codes: list[str], frozen: bool = False) -> str:
    if frozen:
        return "Didn't match what she said"
    for code, name in _STOP_NAMES.items():
        if code in codes:
            return name
    return "Safety rules"


class KernelError(Exception):
    def __init__(self, message: str, code: str = "KERNEL_ERROR"):
        super().__init__(message)
        self.code = code


class Kernel:
    def __init__(self, wallet: Wallet, audit: AuditLog, tokens: TokenService | None = None,
                 authenticator: MockAuthenticator | None = None, allow_mock: bool | None = None,
                 registry: CredentialRegistry | None = None, gateway: ExecutionGateway | None = None,
                 require_verification: bool = True, risk_hour: int | None = None):
        self.risk_hour = risk_hour  # injectable clock hour for isolated lab fixtures only
        self.wallet = wallet
        self.audit = audit
        self.tokens = tokens or TokenService()
        self.authenticator = authenticator or MockAuthenticator()
        # The demo-string authenticator is OFF unless explicitly allowed: payments need a device signature.
        self.allow_mock = (settings.auth_mode == "mock") if allow_mock is None else allow_mock
        self.registry = registry or CredentialRegistry()
        # No gateway passed: an isolated in-memory simulator. app/runtime.py is the only place that builds a real one.
        self.gateway = gateway or ExecutionGateway(wallet, self.registry, audit, allow_unsigned=self.allow_mock)
        self.approvals = self.gateway.approvals
        # No independent-verifier verdict -> the draft can't be signed (fail closed)
        self.require_verification = require_verification

    # ------------------------------------------------------------------ Credentials
    def register_key(self, s: Session, jwk: dict) -> str:
        """Register the browser's public signing key for this session (once). Returns the key id."""
        if not browser_key_allowed(self.registry):
            raise KernelError(BIOMETRIC_REQUIRED, "BIOMETRIC_REQUIRED")
        try:
            p256.public_point(jwk)
        except p256.InvalidKey as e:
            raise KernelError(str(e), "INVALID_KEY") from e
        public = {k: jwk[k] for k in ("kty", "crv", "x", "y")}
        if s.public_key and s.public_key != public:
            raise KernelError("This session already has a different signing key.", "KEY_EXISTS")
        if not s.public_key:
            s.public_key = public
            self.registry.set_device_key(s.session_id, public)
            self.audit.append("device.key_registered", s.session_id, {"key_id": p256.key_fingerprint(public),
                                                                       "alg": "ECDSA P-256"})
        return p256.key_fingerprint(public)

    def passkey_status(self, user_id: str, site: Site) -> dict:
        keys = self.registry.passkeys(user_id, site.rp_id)
        return {"enrolled": bool(keys), "credential_ids": [k["credential_id"] for k in keys],
                "key_ids": [webauthn.key_id(k["public_key"]) for k in keys]}

    def passkey_options(self, s: Session, site: Site) -> dict:
        if self.registry.has_passkey(s.user_id):
            raise KernelError("Verify and remove your existing passkey before replacing it.", "PASSKEY_EXISTS")
        challenge = secrets.token_bytes(32)
        s.passkey_reg = {"challenge": webauthn.b64u(challenge), "expires_at": time.time() + settings.challenge_ttl,
                         "rp_id": site.rp_id}
        user = self.wallet.user(s.user_id)
        return {"challenge": s.passkey_reg["challenge"], "rp_id": site.rp_id, "rp_name": "Star family payments",
                "user_id": webauthn.b64u(hashlib.sha256(s.user_id.encode()).digest()[:16]),
                "user_name": s.user_id, "display_name": user["name"],
                "exclude": self.passkey_status(s.user_id, site)["credential_ids"]}

    def passkey_register(self, s: Session, site: Site, credential_id: str, client_data_json: str,
                         attestation_object: str) -> str:
        if self.registry.has_passkey(s.user_id):
            raise KernelError("An existing passkey cannot be replaced without verification.", "PASSKEY_EXISTS")
        reg = s.passkey_reg
        if not reg or reg["expires_at"] < time.time() or reg["rp_id"] != site.rp_id:
            raise KernelError("The fingerprint set-up expired. Please try again.", "EXPIRED")
        try:
            cred = webauthn.verify_registration(
                credential_id=credential_id, client_data_json=client_data_json, attestation_object=attestation_object,
                challenge=webauthn.unb64u(reg["challenge"]), rp_id=site.rp_id, origins=set(site.origins))
        except webauthn.WebAuthnError as e:
            raise KernelError(str(e), e.code) from e
        s.passkey_reg = None
        self.registry.add_passkey(s.user_id, cred)
        key_id = webauthn.key_id(cred["public_key"])
        self.audit.append("device.passkey_enrolled", s.session_id, {"key_id": key_id, "rp_id": site.rp_id,
                                                                     "alg": f"WebAuthn {cred['public_key'].get('alg', 'ES256')}, user verification required"})
        return key_id

    def passkey_remove_challenge(self, s: Session, site: Site) -> str:
        payload = build_settings_challenge(s.session_id, s.user_id, {"remove_passkeys": site.rp_id})
        s.setting_challenges["remove_passkeys"] = payload
        return payload

    def passkey_remove(self, s: Session, site: Site, assertion: dict | None = None) -> int:
        issued = s.setting_challenges.get("remove_passkeys")
        if not issued or not isinstance(assertion, dict) or assertion.get("payload") != issued:
            raise KernelError("Verify your identity before removing a passkey.", "AUTH_REQUIRED")
        if json.loads(issued)["expires_at"] < time.time():
            raise KernelError("This confirmation expired.", "EXPIRED")
        rec = self._signature(s, issued, assertion, site)
        if rec["method"] != "passkey":
            raise KernelError("An enrolled passkey is required.", "AUTH_REQUIRED")
        s.setting_challenges.pop("remove_passkeys", None)
        n = self.registry.remove_passkeys(s.user_id, site.rp_id)
        self.audit.append("device.passkey_removed", s.session_id, {"rp_id": site.rp_id, "count": n})
        return n

    def _signature(self, s: Session, payload: str, assertion: dict, site: Site) -> dict:
        try:
            rec = check_signature(self.registry, user_id=s.user_id, session_id=s.session_id, payload=payload,
                                  assertion=assertion, site=site)
        except SignatureError as e:
            raise KernelError(str(e), e.code) from e
        if rec["method"] == "passkey":
            self.registry.bump_count(s.user_id, site.rp_id, rec["credential_id"], rec.get("sign_count", 0))
        return rec

    # ------------------------------------------------------------------ Draft
    def _target(self, s: Session, kind: str, payee_id: str, offer_id, quantity, invoice_id, parent_payment_id, amount_cents):
        """Whatever the card is about, resolved by code from authoritative records. What the model may pass is only an id.
        Returns (who, name, handle, fields, ground_kwargs)."""
        if kind == "purchase":
            offer = catalogue.offer(offer_id or "")
            merchant = catalogue.merchant(offer["merchant_id"]) if offer else None
            if not offer or not merchant:
                raise KernelError(f"offer_id={offer_id} is not in the catalogue. Call search_catalogue first.")
            qty = max(1, int(quantity or 1))
            cents = offer["price_cents"] * qty
            item = {"offer_id": offer["offer_id"], "name": offer["name"], "quantity": qty, "unit_cents": offer["price_cents"]}
            who = {"name": merchant["name"], "aliases": offer["aliases"]}
            return who, merchant["name"], "", {"payee_id": merchant["merchant_id"], "merchant_id": merchant["merchant_id"],
                                               "merchant_name": merchant["name"], "items": [item], "amount_cents": cents}, \
                {"catalogue_cents": cents}
        if kind == "refund":
            parent = self.wallet.payment(parent_payment_id or "")
            if not parent or parent["user_id"] != s.user_id:
                raise KernelError(f"payment_id={parent_payment_id} is not one of this customer's payments. Call list_refundable.")
            left = parent["amount_cents"] - parent.get("refunded_cents", 0)
            cents = left if amount_cents is None else amount_cents
            names = [i["name"] for i in parent["card"].get("items", [])]
            aliases = [a for i in parent["card"].get("items", []) for a in (catalogue.offer(i.get("offer_id", "")) or {}).get("aliases", [])]
            payee = self.wallet.payee(s.user_id, parent["card"].get("payee_id", "")) or {}
            who = {"name": parent["counterparty_name"], "aliases": names + aliases + payee.get("aliases", [])}
            rail = "payout_cancel" if parent["rail"] == "payouts" else "refund"
            return who, parent["counterparty_name"], "", {
                "payee_id": parent["card"].get("payee_id", ""), "parent_payment_id": parent["payment_id"], "rail": rail,
                "capture_id": parent["paypal"].get("capture_id") or None, "amount_cents": cents}, {"payment_cents": left}
        if kind == "bill":
            inv = self.wallet.invoice(s.user_id, invoice_id or "")
            if not inv:
                raise KernelError(f"invoice_id={invoice_id} is not one of this customer's invoices. Call list_invoices.")
            who = self.wallet.payee(s.user_id, inv["payee_id"]) or {"name": inv["invoicer_name"], "aliases": []}
            return who, inv["invoicer_name"], "", {"payee_id": inv["payee_id"], "invoice_id": inv["invoice_id"],
                                                   "invoice_number": inv["number"], "amount_cents": inv["amount_cents"]}, {"bill": inv}
        who = self.wallet.payee(s.user_id, payee_id)
        name = who["name"] if who else "(unknown payee)"
        handle = (who or {}).get("paypal_email", "")
        return who, name, self.gateway.receiver_for(who) if who and kind in ("transfer", "money_request") else handle, \
            {"payee_id": payee_id, "amount_cents": amount_cents}, {}

    def create_draft(self, s: Session, *, kind: str, payee_id: str = "", amount_cents: int | None = None,
                     invoice_id: str | None = None, offer_id: str | None = None, quantity: int | None = None,
                     parent_payment_id: str | None = None, note: str = "", taint: list[str] | None = None,
                     local_hour: int | None = None, evidence: str = "", verification: dict | None = None,
                     heard: tuple[str, str] | None = None) -> Draft:
        s.settle()
        if s.phase != Phase.IDLE:
            raise KernelError("There is already a payment waiting for confirmation. Please confirm or cancel it first.", "PENDING_DRAFT")
        from app.agent.request_guard import problem
        for utterance in s.task_utterances():
            error = problem(utterance)
            if error:
                raise KernelError(error, "REQUEST_UNSUPPORTED")
        who, name, handle, fields, ground_kw = self._target(s, kind, payee_id, offer_id, quantity, invoice_id, parent_payment_id, amount_cents)
        now = time.time()
        draft = Draft(draft_id="d_" + uuid.uuid4().hex[:10], user_id=s.user_id, kind=kind, rail=fields.pop("rail", _RAIL.get(kind, "")),
                      from_account=WALLET_ACCOUNT, payee_name=name, payee_handle=handle, note=note[:120], created_at=now,
                      expires_at=now + settings.draft_ttl, nonce=secrets.token_hex(6), taint=taint or [],
                      plan_id=s.plan_id, scam_level=s.scam_level.value, **fields)
        amount_cents = draft.amount_cents
        utterances = user_utterances(s.messages)
        g = ground(utterances, who, amount_cents, kind, model_quote=evidence, heard=heard,
                   proposal=confirmed_proposal(s.messages), **ground_kw)
        latest_user = next((m for m in reversed(s.messages) if m.get("role") == "user"), {})
        parent_id = latest_user.get("reference_parent_id")
        parent = next((r for r in s.receipts if parent_id and r.get("card", {}).get("draft_id") == parent_id), None)
        if parent and parent.get("status") == "SUCCESS" and parent["card"]["amount_cents"] == amount_cents * 2:
            g.amount_source = "derived"
            g.derived = f"Half of the signed payment {parent_id}"
            g.quote = latest_user.get("raw_content", g.quote)[:160]
        # A model-planner step whose amount the code worked out ("half of it"): the draft must use exactly that number
        resolved = latest_user.get("resolved") or {}
        draft.dependency_ids = list(dict.fromkeys(([parent_id] if parent_id else []) + resolved.get("dependency_ids", [])))
        if resolved.get("amount_cents") is not None:
            if amount_cents == resolved["amount_cents"]:
                g.amount, g.amount_source, g.derived = True, "derived", resolved["why"]
            else:
                g.amount, g.amount_source = False, "derived mismatch"
                g.derived = f"{resolved['why']}, which is {fmt_money(resolved['amount_cents'])}"
            g.quote = latest_user.get("raw_content", g.quote)[:160]
        draft.grounding, draft.you_said = g.as_dict(), g.quote
        from app.kernel.voice import task_audio
        draft.you_said_audio = task_audio(s)            # the recordings behind these words, signed with the card
        level = prot.effective(self.wallet, s.user_id, now)
        said = " ".join(s.task_utterances()[-6:])
        draft.trust = trust.check(draft, self.wallet, protection=level, scam_level=s.scam_level.value, said=said)
        draft.digest = compute_digest(draft)
        ctx = rules.RuleContext(now=now, scam_level=s.scam_level, scam_reasons=list(s.scam_reasons), said=said,
                                local_hour=local_hour if local_hour is not None else self.risk_hour, grounding=g, protection=level,
                                rail_error=self.gateway.rail_error())
        draft.risk = rules.evaluate(draft, self.wallet, ctx)

        # Dual-engine audit: compare with what the independent verifier heard (deterministic comparison)
        if verification is not None:
            draft.verification = {**crosscheck.compare(draft, verification.get("extracted") or {}, self.wallet),
                                  "engine": verification.get("engine", "?"),
                                  "extracted": verification.get("extracted"),
                                  "parallel": verification.get("parallel", False)}
        elif self.require_verification:
            draft.verification = {"status": "missing", "mismatches": [], "fields": {}}

        s.drafts[draft.draft_id] = draft
        s.current_draft_id = draft.draft_id
        s.transition(Phase.DRAFTED)
        frozen = draft.verification.get("status") == "mismatch"
        if draft.risk.level == RiskLevel.BLOCK:
            s.transition(Phase.BLOCKED)
        elif frozen:
            s.transition(Phase.FROZEN)
        elif draft.risk.level == RiskLevel.HIGH:
            draft.cooling_until = now + draft.risk.cooldown_seconds
            s.transition(Phase.COOLING)
        else:
            s.transition(Phase.AWAITING_AUTH)

        s.add_trace("evidence", f"Grounded in the user's words: payee {'✓' if g.payee else '✗'} · "
                                f"amount {'✓' if g.amount else '✗'} ({g.amount_source})", g.as_dict())
        if draft.verification:
            v = draft.verification
            marks = " · ".join(f"{k} {'✓' if st == 'match' else '✗' if st == 'mismatch' else '–'}"
                               for k, st in v.get("fields", {}).items())
            s.add_trace("verify", f"Independent verifier ({v.get('engine', '-')}{', ran in parallel' if v.get('parallel') else ''}): "
                                  f"{'MISMATCH → frozen' if frozen else v['status'].upper()} · {marks}", v)
        s.add_trace("rule", f"Rule grading: {draft.risk.level.value} (protection: {level})",
                    {"hits": draft.risk.rule_hits, "ruleset": rules.RULESET_VERSION})
        if draft.trust.get("codes"):
            self.audit.append("trust.check", s.session_id, {"draft_id": draft.draft_id, "codes": draft.trust["codes"],
                                                             "level": draft.trust["level"]})
        self.audit.append("draft.created", s.session_id, {
            "draft": payments.masked_card(draft.card_fields()), "digest": draft.digest, "risk": draft.risk.model_dump(mode="json"),
            "grounding": draft.grounding, "verification": draft.verification, "protection": level,
            "ruleset": rules.RULESET_VERSION})
        if frozen:
            self.audit.append("draft.frozen", s.session_id, {"draft_id": draft.draft_id,
                                                             "mismatches": draft.verification["mismatches"]})
        self.record_decision(s, draft)
        self._family_side(s, draft, frozen)
        return draft

    def _family_side(self, s: Session, draft: Draft, frozen: bool) -> None:
        """What Anna sees: a stop raises an alert; a payment that needs her waits on her device with the exact card."""
        codes = set(draft.risk.rule_hits)
        if draft.risk.level == RiskLevel.BLOCK or frozen:
            self.wallet.add_stop(s.user_id, kind=draft.kind, to=draft.payee_name, amount_cents=draft.amount_cents,
                                 status="FROZEN" if frozen else "STOPPED", stopped_by=stop_name(draft.risk.rule_hits, frozen),
                                 why=" ".join((draft.verification.get("mismatches") if frozen else draft.risk.blocking) or draft.risk.reasons),
                                 codes=draft.risk.rule_hits, draft_id=draft.draft_id, digest=draft.digest, session_id=s.session_id)
        if draft.risk.level == RiskLevel.BLOCK and codes & {"R84_INSTRUCTION_IN_DATA", "R80_INVOICE_PHONE", "R91_PRICE_ANOMALY",
                                                            "R92_MERCHANT_FLAGGED", "R94_GIFT_CARD", "R96_REFUND_SCAM"}:
            self.alert(s, "stop", "stopped", f"Star stopped {fmt_money(draft.amount_cents)} to {draft.payee_name}. "
                       + " ".join(draft.risk.blocking[:1]), draft.draft_id)
        if draft.risk.level == RiskLevel.BLOCK or frozen or not contract.guardian_required(draft.card_fields(), self.wallet):
            return
        self.gateway.guardian.request(s, draft)
        s.add_trace("guard", "This payment needs the family guardian's approval on their own device", {"draft_id": draft.draft_id})

    def alert(self, s: Session, level: str, kind: str, text: str, draft_id: str = "") -> None:
        self.wallet.add_alert(s.user_id, level, kind, text, draft_id)
        self.audit.append("guardian.alert", s.session_id, {"level": level, "kind": kind, "draft_id": draft_id})

    def record_decision(self, s, draft, **kwargs):
        result = contract.decision(draft, s, self.wallet, self.approvals, **kwargs)
        draft.decision = result
        self.audit.append("contract.decision", s.session_id, result)
        return result

    def approve_guardian(self, s, draft_id, digest, signed: dict | None = None):
        """Only the guardian's signed HTTP endpoint may call this; never an agent tool. It can't unlock a BLOCK or FROZEN
        draft (those are refused here and by every later gate) and it can't start anything (I9)."""
        draft = self._get_draft(s, draft_id)
        if s.phase not in (Phase.COOLING, Phase.AWAITING_AUTH) or draft.expires_at <= time.time():
            raise KernelError("This payment is no longer waiting for approval.", "GUARDIAN_NOT_PENDING")
        if digest != draft.digest or compute_digest(draft) != digest:
            raise KernelError("This approval is for a different card.", "DIGEST_MISMATCH")
        if draft.verification.get("status") != "match":
            raise KernelError("The intent check must pass first.", "NOT_VERIFIED")
        if draft.risk and draft.risk.level == RiskLevel.BLOCK:
            raise KernelError("A stopped payment can't be approved.", "BLOCKED")
        self._recheck_risk(s, draft)
        if not contract.guardian_required(draft.card_fields(), self.wallet):
            raise KernelError("This payment does not need family approval.", "GUARDIAN_NOT_REQUIRED")
        rec = self.approvals.approve(s.user_id, s.session_id, draft_id, digest, draft.expires_at, signed)
        try:
            self.audit.append("guardian.approved", s.session_id, {k: rec[k] for k in ("approval_id", "draft_id", "digest", "method")})
            self.record_decision(s, draft)
        except Exception:
            self.approvals.revoke(s.session_id, draft_id)
            raise
        return rec

    def deny_guardian(self, s, draft_id, digest, signed: dict | None = None):
        draft = self._get_draft(s, draft_id)
        if s.phase in (Phase.EXECUTING, Phase.EXECUTED):
            raise KernelError("This payment has already been handed to PayPal.", "ALREADY_SENT")
        if digest != draft.digest:
            raise KernelError("This decision is for a different card.", "DIGEST_MISMATCH")
        rec = self.approvals.deny(s.user_id, s.session_id, draft_id, digest, signed)
        self.audit.append("guardian.denied", s.session_id, {k: rec[k] for k in ("approval_id", "draft_id", "digest")})
        if s.phase in (Phase.DRAFTED, Phase.COOLING, Phase.AWAITING_AUTH):
            s.challenges.pop(draft_id, None)
            s.transition(Phase.CANCELLED)
            self.record_decision(s, draft, stage="cancelled", rejection="GUARDIAN_DENIED")
            self.wallet.add_stop(s.user_id, kind=draft.kind, to=draft.payee_name, amount_cents=draft.amount_cents, status="DENIED",
                                 stopped_by="Your family said no", why="The family guardian denied this exact card on their own device.",
                                 codes=["GUARDIAN_DENIED"], draft_id=draft.draft_id, digest=draft.digest, session_id=s.session_id)
        return rec

    def _contract_gate(self, s, draft, *, signature=False, verify_answer=False, stage="before_authorization"):
        record = self.record_decision(s, draft, signature=signature, verify_answer=verify_answer, stage=stage)
        if record["checks"]["plan"]["status"] == "fail":
            raise KernelError("An earlier payment has not completed. Nothing can move.", "DEPENDENCY_NOT_COMPLETED")
        status = record["checks"]["guardian_approval"]["status"]
        if status == "fail":
            raise KernelError("Your family said no to this payment.", "GUARDIAN_DENIED")
        if status == "pending":
            raise KernelError("Your family guardian must approve this exact payment first.", "GUARDIAN_APPROVAL_REQUIRED")

    # ------------------------------------------------------------------ Challenge
    def issue_challenge(self, s: Session, draft_id: str, shown_digest: str,
                        verify_answer: bool | None = None) -> str:
        """Hand the device the exact bytes to sign. Issuing a new challenge invalidates the previous one."""
        draft = self._get_draft(s, draft_id)
        self._refresh_cooling(s, draft)
        self._precheck(s, draft, shown_digest, verify_answer)
        if not s.public_key and not self.registry.has_passkey(s.user_id):
            raise KernelError("This device has no registered signing key yet.", "NO_KEY")
        payload = build_challenge(s.session_id, draft)
        s.challenges[draft_id] = payload
        self.audit.append("auth.challenge_issued", s.session_id,
                          {"draft_id": draft_id, "payload_sha256": hashlib.sha256(payload.encode()).hexdigest()})
        return payload

    def _precheck(self, s: Session, draft: Draft, shown_digest: str, verify_answer: bool | None) -> None:
        if s.phase != Phase.AWAITING_AUTH:
            raise KernelError(self._phase_message(s, draft), "NOT_AWAITING_AUTH")
        if shown_digest != draft.digest or compute_digest(draft) != draft.digest:
            self.audit.append("auth.rejected", s.session_id, {"draft_id": draft.draft_id, "reason": "digest_mismatch"})
            raise KernelError("The confirmation card doesn't match the transaction. Rejected.", "DIGEST_MISMATCH")
        self._recheck_risk(s, draft)
        if self.require_verification and draft.verification.get("status") != "match":
            raise KernelError("The second reading hasn't confirmed this payment, so it can't be signed.",
                              "NOT_VERIFIED")
        if draft.risk and draft.risk.verify_question and verify_answer is not True:
            raise KernelError("Please answer the verification question first.", "VERIFY_REQUIRED")
        self._contract_gate(s, draft, verify_answer=verify_answer)

    # ------------------------------------------------------------------ Authorize
    def authorize(self, s: Session, draft_id: str, shown_digest: str, assertion,
                  verify_answer: bool | None = None, site: Site = DEFAULT_SITE) -> str:
        """assertion = a passkey assertion {"type": "webauthn", ...} or a browser-key {"payload", "signature"},
        over the exact challenge. (A demo string such as "demo-fingerprint" only when allow_mock is on.)"""
        draft = self._get_draft(s, draft_id)
        self._refresh_cooling(s, draft)
        self._precheck(s, draft, shown_digest, verify_answer)
        if isinstance(assertion, dict):
            method = self._verify_payment_signature(s, draft, assertion, site)
        elif self.allow_mock and isinstance(assertion, str) and self.authenticator.verify(assertion):
            method = "mock:" + assertion
        else:
            self.audit.append("auth.rejected", s.session_id, {"draft_id": draft_id, "reason": "no_valid_signature"})
            raise KernelError("A valid signature from your device is required.", "AUTH_FAILED")
        token = self.tokens.issue(draft_id, shown_digest)
        label = {"passkey": "Server verified the passkey: the device checked your fingerprint / face and signed the exact card",
                 "device_key": "Server verified the browser-key signature over the exact card (fingerprint simulated for the demo)"}.get(
            method, "User confirmed (demo authenticator)")
        s.add_trace("sign", label, {"digest": shown_digest[:16] + "…", "method": method})
        self.audit.append("auth.granted", s.session_id, {"draft_id": draft_id, "digest": shown_digest,
                                                         "method": method})
        self.record_decision(s, draft, stage="authorized", signature=isinstance(assertion, dict), verify_answer=True)
        return token

    def _verify_payment_signature(self, s: Session, draft: Draft, assertion: dict, site: Site) -> str:
        def reject(reason: str, code: str):
            self.audit.append("auth.rejected", s.session_id, {"draft_id": draft.draft_id, "reason": code})
            raise KernelError(reason, code)
        payload = assertion.get("payload")
        issued = s.challenges.get(draft.draft_id)
        if not issued or payload != issued:
            reject("The signed content isn't exactly what was shown to you. Rejected.", "PAYLOAD_MISMATCH")
        signed = json.loads(payload)
        if signed["expires_at"] < time.time():
            s.challenges.pop(draft.draft_id, None)
            reject("This confirmation expired. Please confirm again.", "EXPIRED")
        if (signed["session_id"] != s.session_id or signed["draft_id"] != draft.draft_id
                or signed["digest"] != draft.digest or signed["digest"] != compute_digest(draft)):
            reject("The payment changed after it was shown to you. Rejected.", "DRAFT_CHANGED")
        try:
            rec = self._signature(s, payload, assertion, site)
        except KernelError as e:
            reject(str(e), e.code)
        s.challenges.pop(draft.draft_id, None)          # single use
        s.signatures[draft.draft_id] = rec
        return rec["method"]

    # ------------------------------------------------------------------ Commit
    def commit(self, s: Session, draft_id: str, token: str, return_base: str = "") -> dict:
        draft = self._get_draft(s, draft_id)
        if s.phase != Phase.AWAITING_AUTH:
            raise KernelError(self._phase_message(s, draft), "NOT_AWAITING_AUTH")
        recomputed = compute_digest(draft)
        ok, why = self.tokens.verify_and_consume(token, draft_id, recomputed)
        if not ok:
            self.audit.append("commit.rejected", s.session_id, {"draft_id": draft_id, "reason": why})
            self.record_decision(s, draft, stage="rejected", rejection="TOKEN_INVALID")
            raise KernelError(why, "TOKEN_INVALID")
        # Re-run the rules before executing (the allowance may have changed; prevents time-of-check vs time-of-use gaps)
        self._recheck_risk(s, draft)
        self._contract_gate(s, draft, signature=bool(s.signatures.get(draft_id)), verify_answer=True, stage="pre_execution")
        s.transition(Phase.EXECUTING)      # from here on nobody may say "nothing was sent"
        # PayPal is behind a zero-trust gateway that re-verifies the device signature itself
        try:
            receipt = self.gateway.execute(user_id=s.user_id, session_id=s.session_id, card=draft.card_fields(),
                                           digest=recomputed, auth=s.signatures.get(draft_id),
                                           verification={k: draft.verification.get(k) for k in ("status", "fields", "engine")},
                                           decision=draft.decision, return_base=return_base)
        except GatewayError as e:
            s.transition(Phase.CANCELLED)
            self.record_decision(s, draft, stage="rejected", rejection=e.code)
            raise KernelError(str(e), e.code) from e
        except Exception:
            # A crash, not a refusal. With a write-ahead record the outcome is on that record; with none, nothing moved.
            s.transition(Phase.EXECUTED if self.wallet.payment_by_draft(s.user_id, draft_id) else Phase.CANCELLED)
            raise
        receipt = dict(receipt)
        s.transition(Phase.EXECUTED)
        draft.decision = receipt["verifiable"].get("decision", draft.decision)
        self.approvals.revoke(s.session_id, draft_id)
        s.add_trace("gateway", f"Execution gateway re-verified the device signature on its own, then called PayPal "
                               f"({receipt['paypal_mode']})", {"payment_id": receipt["txn_id"], "state": receipt["state"]})
        s.add_trace("exec", f"{receipt['state']}: {fmt_money(draft.amount_cents)} → {draft.payee_name}",
                    {"payment_id": receipt["txn_id"], "verified": receipt["verified"]})
        if not any(r["txn_id"] == receipt["txn_id"] for r in s.receipts):
            s.receipts.insert(0, receipt["verifiable"])
        return receipt

    def refresh_receipts(self, s: Session) -> None:
        """A receipt's status follows the record: a step counts as done only once PayPal's state is verified."""
        mode = self.gateway.mode_status()["mode"]
        for i, r in enumerate(s.receipts):
            rec = self.wallet.payment(r.get("txn_id", ""))
            if rec:
                s.receipts[i] = payments.receipt_body(rec, mode)

    def _recheck_risk(self, s: Session, draft: Draft) -> None:
        current = rules.evaluate(draft, self.wallet, rules.RuleContext(
            scam_level=s.scam_level, scam_reasons=list(s.scam_reasons), local_hour=self.risk_hour,
            protection=prot.effective(self.wallet, s.user_id), rail_error=self.gateway.rail_error(),
            said=" ".join(s.task_utterances()[-6:])))
        old_hits = set(draft.risk.rule_hits if draft.risk else [])
        new_risk = current.level.rank > draft.risk.level.rank or bool(set(current.rule_hits) - old_hits)
        if current.level == RiskLevel.BLOCK or new_risk:
            s.challenges.pop(draft.draft_id, None)
            s.signatures.pop(draft.draft_id, None)
            s.transition(Phase.CANCELLED)
            draft.risk = current
            self.approvals.revoke(s.session_id, draft.draft_id)
            self.record_decision(s, draft, stage="rejected", rejection="RECHECK_BLOCKED")
            self.audit.append("commit.rejected", s.session_id,
                              {"draft_id": draft.draft_id, "reason": current.rule_hits})
            raise KernelError("The payment conditions changed. Please prepare and review a new payment.", "RECHECK_BLOCKED")

    # ------------------------------------------------------------------ Signed account settings
    def protection_challenge(self, s: Session, to: str) -> str:
        if to != "standard":
            raise KernelError("Only lowering protection needs a signature.", "NOT_NEEDED")
        payload = build_settings_challenge(s.session_id, s.user_id, {"protection": to})
        s.setting_challenges["protection"] = payload
        return payload

    def change_protection(self, s: Session, to: str, assertion: dict | None = None, site: Site = DEFAULT_SITE) -> dict:
        if to == "enhanced":                          # raising (or cancelling a scheduled lowering) is instant
            st = prot.raise_now(self.wallet, s.user_id)
            self.audit.append("settings.protection_raised", s.session_id, {"level": "enhanced"})
            return st
        if to != "standard":
            raise KernelError("Unknown protection level.", "BAD_REQUEST")
        issued = s.setting_challenges.get("protection")
        if not issued or not isinstance(assertion, dict) or assertion.get("payload") != issued:
            raise KernelError("Please confirm this change with your fingerprint.", "PAYLOAD_MISMATCH")
        if json.loads(issued)["expires_at"] < time.time():
            raise KernelError("This confirmation expired. Please try again.", "EXPIRED")
        rec = self._signature(s, issued, assertion, site)
        s.setting_challenges.pop("protection", None)
        st = prot.schedule_lowering(self.wallet, s.user_id)
        self.audit.append("settings.protection_lowering_scheduled", s.session_id,
                          {"effective_at": st["pending"]["effective_at"] if st["pending"] else None,
                           "method": rec["method"], "key_id": rec["key_id"]})
        return st

    # ------------------------------------------------------------------ Freeze: instant on, two people off
    def freeze(self, s: Session, by: str = "customer") -> dict:
        f = self.wallet.set_frozen(s.user_id, True, by)
        self.audit.append("wallet.frozen", s.session_id, {"by": by})
        self.alert(s, "stop", "freeze", f"Payments were frozen by {by}. Nothing can be sent until the freeze is lifted.")
        s.add_trace("guard", f"Payments frozen by {by} (protective, takes effect now)")
        return f

    def lift_freeze_challenge(self, s: Session) -> str:
        payload = build_settings_challenge(s.session_id, s.user_id, {"lift_freeze": True})
        s.setting_challenges["lift_freeze"] = payload
        return payload

    def lift_freeze(self, s: Session, assertion: dict | None, site: Site = DEFAULT_SITE) -> dict:
        """Lifting needs her signature and, when a guardian is paired, the guardian's too (either order)."""
        issued = s.setting_challenges.get("lift_freeze")
        if not issued or not isinstance(assertion, dict) or assertion.get("payload") != issued:
            raise KernelError("Please confirm this with your fingerprint.", "PAYLOAD_MISMATCH")
        if json.loads(issued)["expires_at"] < time.time():
            raise KernelError("This confirmation expired. Please try again.", "EXPIRED")
        self._signature(s, issued, assertion, site)
        s.setting_challenges.pop("lift_freeze", None)
        return self.finish_lift(s.user_id, s.session_id, "owner")

    def finish_lift(self, user_id: str, session_id: str, who: str) -> dict:
        f = self.wallet.mark_lift(user_id, who)
        paired = self.wallet.user(user_id)["guardian"]["status"] == "paired"
        if f["lift"]["owner"] and (f["lift"]["guardian"] or not paired):
            f = self.wallet.set_frozen(user_id, False, "")
            self.audit.append("wallet.unfrozen", session_id, {"by": "customer and guardian" if paired else "customer"})
        return f

    # ------------------------------------------------------------------ Account changes (kernel/changes.py)
    def _wrap(self, fn, *a):
        try:
            return fn(self, *a)
        except changes.ChangeError as e:
            raise KernelError(str(e), e.code) from e

    def request_change(self, s: Session, kind: str, data: dict, evidence: str = "") -> dict:
        return self._wrap(changes.request, s, kind, data, evidence)

    def change_challenge(self, s: Session, change_id: str) -> str:
        return self._wrap(changes.challenge, s, change_id)

    def apply_change(self, s: Session, change_id: str, assertion, site: Site = DEFAULT_SITE) -> dict:
        return self._wrap(changes.apply, s, change_id, assertion, site)

    def cancel_change(self, s: Session, change_id: str) -> None:
        return self._wrap(changes.cancel, s, change_id)

    def lower_limit(self, s: Session, cents: int) -> dict:
        return self._wrap(changes.lower_limit, s, cents)

    # ------------------------------------------------------------------ Cancel
    def cancel(self, s: Session, draft_id: str, reason: str = "user") -> None:
        draft = self._get_draft(s, draft_id)
        if s.phase in (Phase.EXECUTING, Phase.EXECUTED):
            raise KernelError("It's too late to cancel: this payment has already been handed to PayPal.", "ALREADY_SENT")
        if s.phase in (Phase.DRAFTED, Phase.COOLING, Phase.AWAITING_AUTH, Phase.FROZEN):
            s.transition(Phase.CANCELLED)
            self.approvals.revoke(s.session_id, draft_id)
            self.gateway.guardian.clear_request(s.user_id, draft_id)
            self.record_decision(s, draft, stage="cancelled", rejection="Cancelled by user; nothing was sent.")
            self.audit.append("draft.cancelled", s.session_id, {"draft_id": draft.draft_id, "reason": reason})

    # ------------------------------------------------------------------ Helpers
    def _get_draft(self, s: Session, draft_id: str) -> Draft:
        if s.current_draft_id != draft_id or draft_id not in s.drafts:
            raise KernelError("I can't find this pending payment.", "NO_SUCH_DRAFT")
        return s.drafts[draft_id]

    def _refresh_cooling(self, s: Session, draft: Draft) -> None:
        if s.phase == Phase.COOLING and time.time() >= draft.cooling_until:
            s.transition(Phase.AWAITING_AUTH)

    @staticmethod
    def _phase_message(s: Session, draft: Draft) -> str:
        if s.phase == Phase.COOLING:
            left = max(0, int(draft.cooling_until - time.time()))
            return f"Still in the cooling-off period. Please confirm in {left} seconds."
        if s.phase == Phase.BLOCKED:
            return "This payment was blocked and can't be made."
        if s.phase == Phase.FROZEN:
            return "This payment was frozen because it didn't match what you said."
        if s.phase == Phase.EXECUTED:
            return "This payment has already been made."
        return "This payment can't be confirmed right now."


__all__ = ["Kernel", "KernelError", "canonical"]
