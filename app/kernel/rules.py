"""Deterministic rule engine: the same input always gives the same result. The model cannot call or bypass it.

Every rule has a code (for the audit log) and a plain-language reason (for the elderly user).
Merchant and invoice checks live in kernel/trust.py; this engine turns their hits into the risk level.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field

from app.config import settings
from app.kernel import contract, protection as prot, trust
from app.models import Draft, RiskDecision, RiskLevel, fmt_money
from app.wallet.ledger import Wallet

RULESET_VERSION = "rules-2026.11.02-v9"
DUPLICATE_WINDOW = 600
OUTGOING = ("transfer", "bill", "purchase")


_ZONE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+){0,2}")


def valid_timezone(name) -> str | None:
    """An IANA zone name the browser reported ("Asia/Singapore"), or None. Only a name is ever taken from the client,
    never an hour: the hour is worked out here, from the server's clock."""
    if not isinstance(name, str) or len(name) > 64 or not _ZONE_NAME.fullmatch(name):
        return None
    try:
        ZoneInfo(name)
    except Exception:  # noqa: BLE001 - unknown zone, or no zone data on this server
        return None
    return name


def local_hour(now: float, timezone: str = "") -> int:
    """The customer's hour: her browser's zone when it sent a valid one, else CUSTOMER_TIMEZONE, else UTC."""
    for name in (valid_timezone(timezone), settings.customer_timezone):
        if name:
            try:
                return datetime.fromtimestamp(now, ZoneInfo(name)).hour
            except Exception:  # noqa: BLE001 - no time zone data on the server: try the next, then UTC
                continue
    return time.gmtime(now).tm_hour


@dataclass
class RuleContext:
    now: float = field(default_factory=time.time)
    scam_level: RiskLevel = RiskLevel.LOW          # from the scam assessment (can only go up)
    scam_reasons: list[str] = field(default_factory=list)
    local_hour: int | None = None                  # can be injected in tests
    timezone: str = ""                             # the zone her browser reported (validated); "" = CUSTOMER_TIMEZONE
    grounding: object | None = None                # evidence.Grounding: is the draft backed by the user's words
    protection: str = "enhanced"                   # account protection level (kernel/protection.py)
    rail_error: str = ""                           # why PayPal can't be reached at all (missing keys, unsupported mode)
    said: str = ""                                 # the words of this task, for the refund-scam check


def _target(draft: Draft, wallet: Wallet):
    if draft.kind == "purchase":
        return {"name": draft.merchant_name, "type": "merchant"} if draft.merchant_id else None
    if draft.kind == "refund":
        parent = wallet.payment(draft.parent_payment_id or "")
        return {"name": parent["counterparty_name"], "type": "refund"} if parent else None
    return wallet.payee(draft.user_id, draft.payee_id)


def evaluate(draft: Draft, wallet: Wallet, ctx: RuleContext | None = None) -> RiskDecision:
    ctx = ctx or RuleContext()
    level = RiskLevel.LOW
    reasons: list[str] = []
    hits: list[str] = []
    blocking: list[str] = []
    question = None

    def raise_to(new: RiskLevel, code: str, why: str):
        nonlocal level
        level = RiskLevel.max(level, new)
        hits.append(code)
        reasons.append(why)
        if new == RiskLevel.BLOCK:
            blocking.append(why)

    user = draft.user_id
    payee = _target(draft, wallet)
    enhanced = ctx.protection == "enhanced"
    threshold = prot.high_threshold(ctx.protection)
    spends = draft.kind in OUTGOING

    # ---- Hard checks (BLOCK) ----
    if ctx.rail_error:
        raise_to(RiskLevel.BLOCK, "R09_RAIL_UNAVAILABLE", f"I can't reach PayPal from this server, so nothing can be sent. {ctx.rail_error}")
    if wallet.user(user)["frozen"]["on"] and draft.kind != "refund":
        raise_to(RiskLevel.BLOCK, "R14_WALLET_FROZEN", "Payments are frozen right now. Lifting the freeze needs you and your family.")
    if payee is None:
        raise_to(RiskLevel.BLOCK, "R00_UNKNOWN_PAYEE", "This person is not in your saved payees, so I can't send money to them directly.")
    if draft.amount_cents <= 0:
        raise_to(RiskLevel.BLOCK, "R02_BAD_AMOUNT", "The amount must be more than zero.")
    left = wallet.allowance_left(user)
    if spends and draft.amount_cents > left:
        raise_to(RiskLevel.BLOCK, "R03_OVER_ALLOWANCE",
                 f"That's over your Star allowance. You have {fmt_money(left)} left. "
                 "Star can't see your PayPal balance: this is the limit you and your family set.")
    if draft.amount_cents > settings.single_txn_hard_limit * 100:
        raise_to(RiskLevel.BLOCK, "R04_HARD_LIMIT",
                 f"Star doesn't handle payments over {fmt_money(int(settings.single_txn_hard_limit * 100))}.")
    if draft.expires_at < ctx.now:
        raise_to(RiskLevel.BLOCK, "R05_EXPIRED", "This request has expired. Please say it again.")

    # ---- An earlier payment's outcome is still unknown, or the same one just went through ----
    if spends:
        unknown = wallet.open_unknown(user)
        if any(r["card"].get("payee_id") == draft.payee_id or r["amount_cents"] == draft.amount_cents for r in unknown):
            raise_to(RiskLevel.BLOCK, "R60_UNKNOWN_OUTCOME", "I'm still checking with PayPal whether an earlier payment went through. "
                     "Please don't pay again until I know.")
        same = [r for r in wallet.payments_for(user) if r["card"].get("payee_id") == draft.payee_id and r["amount_cents"] == draft.amount_cents
                and r.get("verified") and ctx.now - r["created_at"] <= DUPLICATE_WINDOW and r["state"] in ("SENT", "PAID")]
        if same:
            raise_to(RiskLevel.MEDIUM, "R61_RECENT_DUPLICATE", f"You just paid {payee['name'] if payee else 'them'} this same amount a few minutes ago.")

    # ---- One bill, one payment. The ledger enforces this too, under its own lock ----
    if draft.kind == "bill" and draft.invoice_id and wallet.open_bill_payment(user, draft.invoice_id, draft.draft_id):
        raise_to(RiskLevel.BLOCK, "R62_BILL_IN_PROGRESS", "You've already started paying this bill, so I won't pay it twice. "
                 "You can follow it on your Bills page.")

    # ---- Daily limit: money to other people today ----
    if draft.kind == "transfer" and payee is not None:
        lim = wallet.limits(user)["daily_transfer_cents"]
        used = wallet.transferred_today(user)
        if used + draft.amount_cents > lim:
            remaining = max(0, lim - used)
            raise_to(RiskLevel.BLOCK, "R13_DAILY_LIMIT",
                     f"This is over your daily limit of {fmt_money(lim)}"
                     + (f" (you have {fmt_money(remaining)} left today)." if used else ".")
                     + " You can raise the limit, but a higher limit only starts after a waiting time.")

    if payee is not None and draft.kind == "transfer":
        history = wallet.payee_history(user, draft.payee_id)
        # ---- New payee (HIGH) ----
        if not history:
            raise_to(RiskLevel.HIGH, "R10_NEW_PAYEE", "This is your first payment to this person, so for safety we'll wait a little before confirming.")
        elif contract.payee_ramp(wallet, user, draft.payee_id, draft.amount_cents, draft.draft_id):
            raise_to(RiskLevel.HIGH, "R15_NEW_PAYEE_RAMP",
                     f"You only started paying {payee['name']} recently, and this would be a lot more. "
                     "Scammers often ask for a small payment first, so we'll wait and your family checks this one.")
        elif draft.amount_cents > max(history) * 2:
            raise_to(RiskLevel.MEDIUM, "R11_ABOVE_USUAL",
                     f"This is quite a bit more than you usually send to {payee['name']} ({fmt_money(max(history))}).")
        # ---- Large amount (HIGH) ----
        if draft.amount_cents > threshold * 100:
            raise_to(RiskLevel.HIGH, "R12_ABOVE_THRESHOLD",
                     f"This is more than {fmt_money(int(threshold * 100))}, so we'll wait a little before confirming.")

    # ---- Merchant and invoice checks (kernel/trust.py) ----
    for h in trust.check(draft, wallet, protection=ctx.protection, scam_level=ctx.scam_level.value, said=ctx.said)["hits"]:
        raise_to(RiskLevel(h["level"]), h["code"], h["why"])

    # ---- Time warning (MEDIUM) and hard transaction frequency limit (BLOCK) ----
    if ctx.local_hour is not None:
        hour = ctx.local_hour
    elif settings.fixed_hour is not None:
        hour = settings.fixed_hour
    else:
        # the customer's local time, on the same clock as her screen's greeting; not the server's (Render runs in UTC)
        hour = local_hour(ctx.now, ctx.timezone)
    if enhanced and spends and (hour >= settings.night_start or hour < settings.night_end):
        raise_to(RiskLevel.MEDIUM, "R20_NIGHT", "It's late at night, so let's double-check.")
    # Earlier steps of the same request (one sentence, each step signed on its own) don't count against the limit
    recent = wallet.recent_exec_count(user, settings.velocity_window_min * 60, plan_id=draft.plan_id)
    if spends and recent >= settings.velocity_max:
        raise_to(RiskLevel.BLOCK, "R21_VELOCITY", "Too many payments in a short time. Please wait a little, then ask again.")

    # ---- Fields from untrusted sources (MEDIUM) ----
    if draft.taint:
        raise_to(RiskLevel.MEDIUM, "R30_TAINTED", "Some details came from a picture or a text message. Please check each one.")

    # ---- Scam assessment: one-way escalation, can only get stricter ----
    if ctx.scam_level.rank > RiskLevel.LOW.rank and draft.rail == "payout_cancel":
        # Stopping her own unclaimed payout only brings the money back to her: the alert is shown, it doesn't stop it
        level = RiskLevel.max(level, RiskLevel.MEDIUM)
        hits.append(f"R40_SCAM_{ctx.scam_level.value}_STOP_ONLY")
        reasons.append("There were signs of a scam earlier, so please check this. Stopping it only brings your money back.")
    elif ctx.scam_level.rank > RiskLevel.LOW.rank:
        level = RiskLevel.max(level, ctx.scam_level)
        hits.append(f"R40_SCAM_{ctx.scam_level.value}")
        reasons.extend(ctx.scam_reasons or ["Earlier in this conversation there were signs of a scam, so this needs extra care."])

    # ---- Evidence grounding: the model must not be the only source of the payee / amount ----
    g = ctx.grounding
    if g is not None and payee is not None and g.amount_source == "derived mismatch":
        raise_to(RiskLevel.BLOCK, "R53_WRONG_SHARE",
                 f"You asked for {g.derived}, but this is {fmt_money(draft.amount_cents)}. I won't prepare a different amount.")
    elif g is not None and payee is not None:
        if not g.payee and not g.amount:
            raise_to(RiskLevel.BLOCK, "R52_NOT_REQUESTED",
                     "I can't find this payment in anything you said, so I won't prepare it.")
        elif not g.payee and draft.kind != "refund":      # money coming back to her: the card names it, no wait
            raise_to(RiskLevel.HIGH, "R51_PAYEE_NOT_SAID",
                     f"You didn't mention {payee['name']}. Please check who this money is going to.")
        elif not g.amount:
            raise_to(RiskLevel.MEDIUM, "R50_AMOUNT_NOT_SAID",
                     f"You didn't say the amount, so I filled in {fmt_money(draft.amount_cents)}. Please check it.")

    if level == RiskLevel.MEDIUM and set(hits) <= {"R50_AMOUNT_NOT_SAID"}:
        question = f"Is {fmt_money(draft.amount_cents)} the right amount?"
    elif level == RiskLevel.MEDIUM and draft.kind in ("purchase", "bill", "refund", "money_request"):
        question = "Is this really what you want, and is it your own idea?"
    elif level == RiskLevel.MEDIUM:
        question = "Do you know this person, and is this payment your own idea?"
    verify_kind = None
    if enhanced and {"R10_NEW_PAYEE", "R15_NEW_PAYEE_RAMP"} & set(hits) and level != RiskLevel.BLOCK:
        # The first payment to someone new is how most scams end: ask outright whether someone else is behind it.
        # The card can't be signed without a "yes"; a "no" stops it and points to the scam hotline.
        question = (f"Do you know {payee['name']} yourself, and is sending this money your own idea? "
                    "Say no if someone on the phone or in a message asked you to pay them.")
        verify_kind = "new_payee"
    cooldown = settings.cooldown_seconds if level == RiskLevel.HIGH else 0
    return RiskDecision(level=level, reasons=reasons, rule_hits=hits, blocking=blocking,
                        verify_question=question, verify_kind=verify_kind, cooldown_seconds=cooldown)
