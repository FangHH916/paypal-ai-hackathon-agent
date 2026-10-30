"""The family guardian: Anna's own device, paired to her mother's Star, co-approving the risky payments.

The approval is a signature, not a button press: her device's key signs {draft, digest, decision}, and the execution
gateway re-verifies that signature itself. Bound to the digest, so a changed card invalidates it. A guardian can't start
a payment, and an approval can't unlock anything the rules blocked or the verifier froze (I9).

Alerts appear on the guardian's page. This demo sends no real texts or emails.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time

from app.config import settings
from app.kernel import p256
from app.kernel.signing import canonical, verify_device_signature
from app.models import fmt_money
from app.wallet.payments import activity_row, mask_email

DOMAIN = "STAR-GUARDIAN-V1"
INVITE_TTL = 600
CHALLENGE_TTL = 180


class GuardianError(Exception):
    def __init__(self, message: str, code: str = "GUARDIAN_ERROR"):
        super().__init__(message)
        self.code = code


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class GuardianDesk:
    def __init__(self, wallet, audit=None):
        self.wallet, self.audit = wallet, audit
        self._used: set[str] = set()
        self._lock = threading.Lock()

    def _log(self, event: str, session_id: str, data: dict) -> None:
        if self.audit:
            self.audit.append(event, session_id, data)

    # ---------------------------------------------------------------- pairing
    def invite(self, user_id: str, session_id: str = "") -> str:
        """Called only after Mrs. Lee signed the invite. Returns a one-time token for the link."""
        token = secrets.token_urlsafe(24)

        def change(u):
            u["guardian"]["invite"] = {"token_sha256": _sha(token), "expires_at": time.time() + INVITE_TTL}
        self.wallet.update_user(user_id, change)
        self._log("guardian.invited", session_id, {"expires_in": INVITE_TTL})
        return token

    def pair(self, invite_token: str, jwk: dict) -> dict:
        user_id = self._user_for_invite(invite_token)
        try:
            p256.public_point(jwk)
        except p256.InvalidKey as e:
            raise GuardianError(str(e), "INVALID_KEY") from e
        public = {k: jwk[k] for k in ("kty", "crv", "x", "y")}
        token = secrets.token_urlsafe(32)

        def change(u):
            u["guardian"].update(status="paired", key_jwk=public, key_id=p256.key_fingerprint(public), token_sha256=_sha(token),
                                 invite=None, paired_at=time.time(), removal=None)
        self.wallet.update_user(user_id, change)
        self._log("guardian.paired", "", {"key_id": p256.key_fingerprint(public)})
        return {"token": token, "key_id": p256.key_fingerprint(public), "customer": self.wallet.user(user_id)["name"]}

    def _user_for_invite(self, token: str) -> str:
        sha = _sha(token or "")
        for uid, u in self.wallet.users().items():
            inv = u["guardian"].get("invite")
            if inv and secrets.compare_digest(inv["token_sha256"], sha):
                if inv["expires_at"] < time.time():
                    raise GuardianError("This invite has expired. Please ask for a new one.", "INVITE_EXPIRED")
                return uid
        raise GuardianError("This invite isn't valid.", "INVITE_INVALID")

    def user_for_token(self, token: str) -> str:
        sha = _sha(token or "")
        for uid, u in self.wallet.users().items():
            g = u["guardian"]
            if g["status"] == "paired" and g["token_sha256"] and secrets.compare_digest(g["token_sha256"], sha):
                return uid
        raise GuardianError("This device isn't paired.", "GUARDIAN_UNKNOWN")

    def status(self, user_id: str) -> dict:
        g = self.wallet.user(user_id)["guardian"]
        removal = g.get("removal")
        if removal and removal["effective_at"] <= time.time():
            self.wallet.update_user(user_id, lambda u: u["guardian"].update(status="not_paired", key_jwk=None, key_id="", token_sha256="", removal=None))
            g = self.wallet.user(user_id)["guardian"]
            removal = None
        return {"name": g["name"], "relation": g["relation"], "status": g["status"], "key_id": g["key_id"], "removal": removal}

    def request_removal(self, user_id: str, session_id: str = "") -> dict:
        """Removing the guardian is signed and only starts after a delay: a scammer can't rush it away."""
        eff = time.time() + settings.protection_delay
        self.wallet.update_user(user_id, lambda u: u["guardian"].update(removal={"effective_at": eff}))
        self._log("guardian.removal_scheduled", session_id, {"effective_at": eff})
        return self.status(user_id)

    # ---------------------------------------------------------------- what waits for her
    def request(self, s, draft) -> None:
        reasons = (draft.risk.reasons if draft.risk else [])[:4]
        row = {"draft_id": draft.draft_id, "session_id": s.session_id, "digest": draft.digest, "requested_at": time.time(),
               "expires_at": draft.expires_at, "kind": draft.kind, "payee_name": draft.payee_name,
               "payee_handle": mask_email(draft.payee_handle), "amount_cents": draft.amount_cents, "amount": fmt_money(draft.amount_cents),
               "currency": draft.currency, "you_said": draft.you_said, "items": draft.items, "reasons": reasons,
               "risk_level": draft.risk.level.value if draft.risk else "", "digest_short": draft.digest[:12]}
        self.wallet.update_user(s.user_id, lambda u: u["pending_approvals"].__setitem__(draft.draft_id, row))
        self.wallet.add_alert(s.user_id, "ask", "approval", f"{self.wallet.user(s.user_id)['name']} wants to pay {draft.payee_name} "
                              f"{fmt_money(draft.amount_cents)}. Your approval is needed.", draft.draft_id)

    def clear_request(self, user_id: str, draft_id: str) -> None:
        if draft_id in self.wallet.user(user_id)["pending_approvals"]:
            self.wallet.update_user(user_id, lambda u: u["pending_approvals"].pop(draft_id, None))

    def pending(self, user_id: str) -> list[dict]:
        now = time.time()
        rows = self.wallet.user(user_id)["pending_approvals"]
        for draft_id in [d for d, r in rows.items() if r["expires_at"] < now]:
            self.clear_request(user_id, draft_id)
        return sorted(self.wallet.user(user_id)["pending_approvals"].values(), key=lambda r: r["requested_at"])

    def feed(self, user_id: str) -> dict:
        u = self.wallet.user(user_id)
        return {"customer": u["name"], "guardian": self.status(user_id), "pending": self.pending(user_id),
                "alerts": u["alerts"][:30], "frozen": {k: u["frozen"][k] for k in ("on", "by", "at")},
                "activity": [activity_row(r) for r in self.wallet.payments_for(user_id)][:100],
                "note": "Alerts appear here. This demo doesn't send real texts or emails."}

    # ---------------------------------------------------------------- signed decisions
    def challenge(self, user_id: str, draft_id: str, decision: str) -> str:
        if decision not in ("approve", "deny"):
            raise GuardianError("The decision must be approve or deny.", "BAD_REQUEST")
        row = self.wallet.user(user_id)["pending_approvals"].get(draft_id)
        if not row:
            raise GuardianError("This payment isn't waiting for you any more.", "GUARDIAN_NOT_PENDING")
        return canonical({"domain": DOMAIN, "draft_id": draft_id, "digest": row["digest"], "decision": decision,
                          "nonce": secrets.token_hex(12), "expires_at": int(time.time()) + CHALLENGE_TTL})

    def verify_signed(self, user_id: str, draft_id: str, digest: str, signed: dict, decision: str) -> bool:
        """The check, with no side effects. The gateway calls this itself at execution time."""
        g = self.wallet.user(user_id)["guardian"]
        payload, signature = (signed or {}).get("payload"), (signed or {}).get("signature")
        if g["status"] != "paired" or not g["key_jwk"] or not isinstance(payload, str) or not isinstance(signature, str):
            return False
        try:
            body = json.loads(payload)
        except ValueError:
            return False
        if (body.get("domain"), body.get("draft_id"), body.get("digest"), body.get("decision")) != (DOMAIN, draft_id, digest, decision):
            return False
        return verify_device_signature(g["key_jwk"], payload, signature)

    def decide(self, user_id: str, payload: str, signature: str) -> dict:
        """Check a decision and use it up. Returns the row it was for. Replay of a signature: refused."""
        try:
            body = json.loads(payload)
        except (TypeError, ValueError) as e:
            raise GuardianError("Malformed decision.", "BAD_REQUEST") from e
        draft_id, decision = body.get("draft_id", ""), body.get("decision", "")
        row = self.wallet.user(user_id)["pending_approvals"].get(draft_id)
        if not row:
            raise GuardianError("This payment isn't waiting for you any more.", "GUARDIAN_NOT_PENDING")
        if body.get("expires_at", 0) < time.time():
            raise GuardianError("This confirmation expired. Please try again.", "EXPIRED")
        if body.get("digest") != row["digest"]:
            raise GuardianError("The card changed. This decision doesn't apply to it.", "DIGEST_MISMATCH")
        if not self.verify_signed(user_id, draft_id, row["digest"], {"payload": payload, "signature": signature}, decision):
            raise GuardianError("That signature isn't from the paired guardian device.", "GUARDIAN_SIGNATURE")
        key = _sha(payload)
        with self._lock:
            if key in self._used:
                raise GuardianError("This decision was already used.", "REPLAY")
            self._used.add(key)
        self.clear_request(user_id, draft_id)
        return {**row, "decision": decision, "signed": {"payload": payload, "signature": signature}}

    def sign_lift(self, user_id: str, payload: str, signature: str) -> None:
        """Guardian's half of lifting a freeze: same key, its own purpose string, no draft involved."""
        g = self.wallet.user(user_id)["guardian"]
        try:
            body = json.loads(payload)
        except (TypeError, ValueError) as e:
            raise GuardianError("Malformed request.", "BAD_REQUEST") from e
        if body.get("domain") != DOMAIN + "-LIFT" or body.get("expires_at", 0) < time.time() or not g["key_jwk"] \
                or not verify_device_signature(g["key_jwk"], payload, signature):
            raise GuardianError("That signature isn't from the paired guardian device.", "GUARDIAN_SIGNATURE")

    @staticmethod
    def lift_challenge() -> str:
        return canonical({"domain": DOMAIN + "-LIFT", "nonce": secrets.token_hex(12), "expires_at": int(time.time()) + CHALLENGE_TTL})
