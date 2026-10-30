"""Account changes: saved payees and the daily limit.

Two kinds, decided by whether the change could help someone take money out:

* Risk-raising (add a payee, raise the daily limit): handled exactly like a payment. The agent can only *request* it;
  the details must come from the customer's own words (deterministic grounding, no model); code builds the confirmation
  card; the customer's device signs the exact change; only then is it applied. A raised limit only takes effect after a
  delay, like lowering the account protection.
* Protective (lower the limit): applied at once and logged. Still grounded: the customer must have said the amount, so
  a hidden instruction in some data can't quietly change it.

In a conversation flagged as a likely scam, risk-raising changes are refused outright.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time

from app.config import settings
from app.kernel.evidence import amounts_in, user_utterances
from app.kernel.hotline import hotline_words
from app.kernel.signing import build_settings_challenge, canonical
from app.models import RiskLevel, fmt_money
from app.wallet.payments import EMAIL, mask_email

RAISING = {"add_payee", "raise_limit"}


class ChangeError(Exception):
    def __init__(self, message: str, code: str = "CHANGE_REFUSED"):
        super().__init__(message)
        self.code = code


def _said(s) -> list[str]:
    return user_utterances(s.messages)


def _mentions(utterances: list[str], names: list[str]) -> bool:
    low = " ".join(utterances).lower()
    return any(n and re.search(rf"(?<![a-z]){re.escape(n.lower())}(?![a-z])", low) for n in names)


def _amount_said(utterances: list[str], cents: int) -> bool:
    return any(cents in amounts_in(u) for u in utterances)


def _quote(utterances: list[str], evidence: str) -> str:
    ev = (evidence or "").strip()
    if ev and any(ev.lower() in u.lower() for u in utterances):
        return ev[:160]
    return (utterances[-1] if utterances else "")[:160]


# ---------------------------------------------------------------------------------------------- risk-raising
def request(kernel, s, kind: str, data: dict, evidence: str = "") -> dict:
    """Validate and ground a risk-raising change, then put its confirmation card in front of the customer."""
    if kind not in RAISING:
        raise ChangeError("Unknown change.", "BAD_REQUEST")
    if s.scam_level.rank >= RiskLevel.HIGH.rank:
        raise ChangeError("For your safety I can't add payees or raise limits in this conversation. "
                          f"If someone is telling you to do this, please hang up and call {hotline_words()}.",
                          "SCAM_LOCK")
    wallet, said = kernel.wallet, _said(s)
    rows: list[list[str]] = []
    if kind == "add_payee":
        email = (data.get("email") or "").strip().lower()
        name = (data.get("name") or "").strip()
        if not EMAIL.fullmatch(email) or not any(email in u.lower() for u in said):
            raise ChangeError("The PayPal email must be exactly what you said. Please say it again.", "NOT_SAID")
        if not name or not _mentions(said, [w for w in re.findall(r"[A-Za-z]{3,}", name)]):
            raise ChangeError("Please tell me the person's name.", "NOT_SAID")
        if any(p.get("paypal_email", "").lower() == email or p["name"].lower() == name.lower() for p in wallet.payees(s.user_id)):
            raise ChangeError(f"{name} is already one of your saved people.", "EXISTS")
        data = {"name": name, "email": email, "relation": (data.get("relation") or "").strip()[:30]}
        title = "Add a new person"
        rows = [["Name", data["name"]], ["PayPal email", data["email"]]]
        if data["relation"]:
            rows.append(["Who they are", data["relation"]])
        note = ("Check every letter. Your first payment to a new person waits a little and is checked again, and above "
                f"{fmt_money(int(settings.guardian_threshold * 100))} your family has to agree. If someone you haven't met "
                "asked you to add them, stop and call your family.")
    else:  # raise_limit
        lim = wallet.limits(s.user_id)
        to_cents = int(data["to_cents"])
        if to_cents <= lim["daily_transfer_cents"]:
            raise ChangeError("That isn't higher than your current limit.", "NOT_HIGHER")
        if to_cents > settings.single_txn_hard_limit * 100 * 2:
            raise ChangeError(f"Star doesn't allow a limit above {fmt_money(int(settings.single_txn_hard_limit * 200))}.", "TOO_HIGH")
        if not _amount_said(said, to_cents):
            raise ChangeError("The new limit must be the amount you said. Please tell me the amount.", "NOT_SAID")
        data = {"from_cents": lim["daily_transfer_cents"], "to_cents": to_cents}
        from app.kernel.protection import delay_words
        wait = delay_words()
        title = "Raise your daily limit"
        rows = [["Daily limit now", fmt_money(data["from_cents"])], ["New daily limit", fmt_money(to_cents)],
                ["Takes effect", f"in {wait}"]]
        note = (f"For your safety a higher limit only starts after {wait}. You can keep the old limit at any "
                "time before then. Lowering it again is instant.")
    change_id = "c_" + secrets.token_hex(5)
    quote = _quote(said, evidence)
    card = {"change_id": change_id, "kind": kind, "title": title, "rows": rows, "you_said": quote, "note": note}
    card["digest"] = hashlib.sha256(canonical({"kind": kind, "data": data, "id": change_id}).encode()).hexdigest()
    s.pending_change = {"change_id": change_id, "kind": kind, "data": data, "card": card,
                        "created_at": time.time(), "expires_at": time.time() + settings.draft_ttl}
    s.add_trace("evidence", f"Change grounded in your words: {title}", {"you_said": quote})
    logged = {**data, "email": mask_email(data["email"])} if "email" in data else data
    kernel.audit.append("change.requested", s.session_id, {"change_id": change_id, "kind": kind, "data": logged})
    return card


def challenge(kernel, s, change_id: str) -> str:
    pc = _pending(s, change_id)
    payload = build_settings_challenge(s.session_id, s.user_id,
                                       {"change_id": change_id, "kind": pc["kind"], "data": pc["data"]})
    s.setting_challenges["change:" + change_id] = payload
    return payload


def apply(kernel, s, change_id: str, assertion: dict, site) -> dict:
    pc = _pending(s, change_id)
    issued = s.setting_challenges.get("change:" + change_id)
    if not issued or not isinstance(assertion, dict) or assertion.get("payload") != issued:
        raise ChangeError("Please confirm this change with your fingerprint.", "PAYLOAD_MISMATCH")
    if json.loads(issued)["expires_at"] < time.time():
        raise ChangeError("This confirmation expired. Please try again.", "EXPIRED")
    rec = kernel._signature(s, issued, assertion, site)
    s.setting_challenges.pop("change:" + change_id, None)
    wallet, uid, d = kernel.wallet, s.user_id, pc["data"]
    if pc["kind"] == "add_payee":
        p = wallet.add_payee(uid, d["name"], d["email"], d["relation"])
        reply = (f"Done. {p['name']} ({p['masked_account']}) is now one of your saved people. Your first payment to them "
                 "will wait a little and be checked again.")
        logged = {**d, "email": mask_email(d["email"])}
    else:
        eff = time.time() + settings.protection_delay
        wallet.set_daily_limit(uid, d["to_cents"], effective_at=eff)
        from app.kernel.protection import delay_words
        reply = (f"Done. Your daily limit goes up to {fmt_money(d['to_cents'])} in {delay_words()}. "
                 f"Until then it stays {fmt_money(d['from_cents'])}.")
        logged = d
    s.pending_change = None
    s.add_trace("sign", f"Change signed on your device and applied: {pc['card']['title']}", {"method": rec["method"]})
    kernel.audit.append("change.applied", s.session_id, {"change_id": change_id, "kind": pc["kind"], "data": logged,
                                                          "method": rec["method"], "key_id": rec.get("key_id")})
    return {"reply": reply, "method": rec["method"]}


def cancel(kernel, s, change_id: str) -> None:
    _pending(s, change_id)
    s.pending_change = None
    kernel.audit.append("change.cancelled", s.session_id, {"change_id": change_id})


def _pending(s, change_id: str) -> dict:
    pc = s.pending_change
    if not pc or pc["change_id"] != change_id:
        raise ChangeError("There is no change waiting for confirmation.", "NO_CHANGE")
    if pc["expires_at"] < time.time():
        s.pending_change = None
        raise ChangeError("This request has expired. Please ask again.", "EXPIRED")
    return pc


# ---------------------------------------------------------------------------------------------- protective
def lower_limit(kernel, s, to_cents: int) -> dict:
    lim = kernel.wallet.limits(s.user_id)
    if to_cents >= lim["daily_transfer_cents"]:
        raise ChangeError("That isn't lower than your current limit.", "NOT_LOWER")
    if not _amount_said(_said(s), to_cents):
        raise ChangeError("The new limit must be the amount you said.", "NOT_SAID")
    new = kernel.wallet.set_daily_limit(s.user_id, to_cents)
    kernel.audit.append("limit.lowered", s.session_id, {"from_cents": lim["daily_transfer_cents"], "to_cents": to_cents})
    s.add_trace("guard", f"Daily limit lowered to {fmt_money(to_cents)} (protective, takes effect now)")
    return new
