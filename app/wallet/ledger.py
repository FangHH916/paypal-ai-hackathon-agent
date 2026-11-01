"""The household wallet: Mrs. Lee's fixed demo world, her spending allowance and her payment records.

PayPal holds the money; this module holds what Star needs in order to decide and to remember. Star can't see a PayPal
balance, so the "account" here is an allowance she and her daughter set: rule R03 stops anything over it.

Note: the Agent (model) never calls this directly. The tool gateway reads; writes that move money go only through the
execution gateway (app/wallet/gateway.py), which re-verifies the customer's signature before PayPal hears anything.
"""
from __future__ import annotations

import copy
import itertools
import secrets
import threading
import time
import uuid

from app.config import settings
from app.wallet import catalogue, payments
from app.wallet.storage import LedgerStorage, atomic_write

DEMO_USER = "u_lee"
WALLET_ACCOUNT = "WALLET"
WEEK = 7 * 86400


def _seed_invoice(seed: dict) -> dict:
    return {**seed, "status": "SENT", "paypal_id": "", "view_url": "", "bill_id": seed["invoice_id"]}


def _world_user() -> dict:
    return {
        "name": "Mrs. Lee", "full_name": "Margaret Lee", "lang": "en",
        # Protection level is an ACCOUNT setting, not a screen setting (see kernel/protection.py)
        "protection": {"level": "enhanced", "pending": None},
        # One wallet. balance_cents is what is left of the allowance: spending reserves it, a failure gives it back.
        "accounts": [{"account_id": WALLET_ACCOUNT, "name": "Star allowance", "balance_cents": settings.allowance_cents,
                      "currency": settings.wallet_currency, "type": "allowance", "masked": "Set with your family"}],
        "allowance": {"monthly_cents": settings.allowance_cents},
        "limits": {"daily_transfer_cents": 100000, "pending": None},   # money to other people per day
        "payees": [
            {"payee_id": "p_daughter", "name": "Anna Lee", "relation": "daughter", "type": "person",
             "aliases": ["daughter", "my girl", "anna"], "paypal_email": "anna.lee@family.example",
             "masked_account": "a***@family.example"},
            {"payee_id": "p_son", "name": "David Lee", "relation": "son", "type": "person",
             "aliases": ["son", "david"], "paypal_email": "david.lee@family.example",
             "masked_account": "d***@family.example"},
            # Two grandsons: "my grandson" is ambiguous on purpose, so the agent must ask (with buttons)
            {"payee_id": "p_grandson_ben", "name": "Ben Lee", "relation": "grandson", "type": "person",
             "aliases": ["grandson", "ben"], "paypal_email": "ben.lee@family.example",
             "masked_account": "b***@family.example"},
            {"payee_id": "p_grandson_sam", "name": "Sam Lee", "relation": "grandson", "type": "person",
             "aliases": ["grandson", "sam"], "paypal_email": "sam.lee@family.example",
             "masked_account": "s***@family.example"},
            {"payee_id": "p_power", "name": "Riverside Power", "relation": "bill", "type": "biller",
             "aliases": ["electricity", "electric", "power", "power bill", "riverside", "utilities", "utility"],
             "masked_account": "Bill account ••••5502"},
            {"payee_id": "p_water", "name": "Maple County Water", "relation": "bill", "type": "biller",
             "aliases": ["water", "water bill", "maple county"], "masked_account": "Bill account ••••0718"},
        ],
        "invoices": [_seed_invoice(s) for s in catalogue.INVOICE_SEEDS],
        "invoice_tag": "star-" + secrets.token_hex(6),
        # Transaction history: negative = outgoing, positive = incoming. All before this demo began.
        "transactions": [
            {"date": "2026-10-01", "desc": "Pension deposit", "amount_cents": 98000, "payee_id": None},
            {"date": "2026-09-28", "desc": "Transfer to Anna Lee", "amount_cents": -20000, "payee_id": "p_daughter"},
            {"date": "2026-09-15", "desc": "Transfer to David Lee", "amount_cents": -10000, "payee_id": "p_son"},
            {"date": "2026-09-20", "desc": "Transfer to Ben Lee", "amount_cents": -5000, "payee_id": "p_grandson_ben"},
            {"date": "2026-09-18", "desc": "Transfer to Sam Lee", "amount_cents": -5000, "payee_id": "p_grandson_sam"},
            {"date": "2026-09-12", "desc": "Riverside Power", "amount_cents": -8230, "payee_id": "p_power"},
            {"date": "2026-09-01", "desc": "Pension deposit", "amount_cents": 98000, "payee_id": None},
            {"date": "2026-08-27", "desc": "Transfer to Anna Lee", "amount_cents": -20000, "payee_id": "p_daughter"},
            {"date": "2026-08-20", "desc": "Green Basket Grocers", "amount_cents": -6450, "payee_id": None},
        ],
        "guardian": {"name": "Anna Lee", "relation": "daughter", "status": "not_paired", "key_jwk": None, "key_id": "",
                     "token_sha256": "", "invite": None},
        "pending_approvals": {},     # draft id -> what the guardian's page shows
        "alerts": [],                # family alerts. In-app only: this demo sends no real texts or emails.
        "stops": [],                 # what Star stopped: blocked, frozen, denied, scam. Rows of the Activity grid.
        "frozen": {"on": False, "by": "", "at": None, "lift": {"owner": False, "guardian": False}},
    }


class Wallet(LedgerStorage):
    def __init__(self, path=None):
        self._lock = threading.RLock()
        self._tick, self._last_seen = itertools.count(1), {}      # who was active last, for choosing whom to forget
        self.path = None
        self.world, self.payments, self.recent_exec_times, self.outbox, self.webhook_events = {}, {}, {}, [], {}
        self.reset()
        self.path = path
        self._load()

    @atomic_write
    def reset(self):
        with self._lock:
            self.world = {"users": {DEMO_USER: _world_user()}}
            self.payments: dict[str, dict] = {}         # payment id -> record
            self.recent_exec_times: dict[str, list[float]] = {}
            self.outbox = []
            self.webhook_events: dict[str, dict] = {}   # PayPal event id -> first seen (dedup survives a restart)

    # ---------- Demo customers per visitor ----------
    # On a shared online demo every browser gets its own copy of Mrs. Lee ("u_lee~<visitor id>"), so one tester's
    # payments, resets and passkey never affect another's. Locally (no visitor id) everyone is plain "u_lee".
    MAX_VISITORS = 300       # every write still snapshots the whole world (storage.py), so this also bounds its cost

    @staticmethod
    def visitor_user_id(visitor: str) -> str:
        return f"{DEMO_USER}~{visitor}"

    def _ensure(self, user_id: str) -> None:
        if not user_id.startswith(DEMO_USER + "~"):
            return
        self._last_seen[user_id] = next(self._tick)
        if user_id in self.world["users"]:
            return
        with self._lock:
            if user_id not in self.world["users"]:
                self._make_room()
                self.world["users"][user_id] = _world_user()

    def _guarded(self, world_user: dict) -> bool:
        return world_user["frozen"]["on"] or world_user["guardian"]["status"] == "paired"

    def _make_room(self) -> None:
        """At the cap the idlest visitor goes, along with their records. A world someone froze or paired a guardian on is
        only given up when every other one is too."""
        users = self.world["users"]
        visitors = [u for u in users if "~" in u]
        over = len(visitors) - self.MAX_VISITORS + 1
        if over <= 0:
            return
        visitors.sort(key=lambda u: (self._guarded(users[u]), self._last_seen.get(u, 0)))
        for old in visitors[:over]:
            users.pop(old, None)
            self._last_seen.pop(old, None)
            self.recent_exec_times.pop(old, None)
            self.payments = {k: r for k, r in self.payments.items() if r.get("user_id") != old}

    @atomic_write
    def reset_user(self, user_id: str) -> None:
        """Reset one demo customer's wallet, payees, invoices, guardian and records (nobody else's)."""
        with self._lock:
            if user_id not in self.world["users"] and "~" in user_id:
                self._make_room()
            self.world["users"][user_id] = _world_user()
            self.recent_exec_times.pop(user_id, None)
            self.payments = {k: r for k, r in self.payments.items() if r.get("user_id") != user_id}

    # ---------- Read-only ----------
    def user(self, user_id: str) -> dict:
        self._ensure(user_id)
        return self.world["users"][user_id]

    def users(self) -> dict:
        return self.world["users"]

    def accounts(self, user_id: str) -> list[dict]:
        return self.user(user_id)["accounts"]

    def account(self, user_id: str, account_id: str) -> dict | None:
        return next((a for a in self.accounts(user_id) if a["account_id"] == account_id), None)

    def allowance_left(self, user_id: str) -> int:
        return self.accounts(user_id)[0]["balance_cents"]

    def payees(self, user_id: str) -> list[dict]:
        return self.user(user_id)["payees"]

    def payee(self, user_id: str, payee_id: str) -> dict | None:
        found = next((p for p in self.payees(user_id) if p["payee_id"] == payee_id), None)
        if found or not str(payee_id).startswith("invoicer:"):
            return found
        # Someone who sent an invoice but whom the household doesn't know: the name comes from the invoice itself
        inv = next((i for i in self.invoices(user_id, only_open=False) if i["payee_id"] == payee_id), None)
        return {"payee_id": payee_id, "name": inv["invoicer_name"], "relation": "unknown sender", "type": "biller",
                "aliases": [inv["invoicer_name"].lower(), inv["invoicer_name"].split()[0].lower(), inv["title"].lower()],
                "masked_account": payments.mask_email(inv["invoicer_email"])} if inv else None

    # Invoices: `only_open` hides paid and cancelled ones. They arrive through PayPal (app/wallet/invoices.py).
    def invoices(self, user_id: str, only_open: bool = True) -> list[dict]:
        rows = self.user(user_id)["invoices"]
        return [i for i in rows if i["status"] in ("SENT", "UNPAID")] if only_open else rows

    def invoice(self, user_id: str, invoice_id: str) -> dict | None:
        return next((i for i in self.user(user_id)["invoices"] if i["invoice_id"] == invoice_id), None)

    def transactions(self, user_id: str, limit: int = 10) -> list[dict]:
        return self.user(user_id)["transactions"][:limit]

    def limits(self, user_id: str) -> dict:
        lim = self.user(user_id)["limits"]
        p = lim.get("pending")
        if p and p["effective_at"] <= time.time():          # a scheduled raise takes effect
            lim["daily_transfer_cents"], lim["pending"] = p["to_cents"], None
        return lim

    def transferred_today(self, user_id: str) -> int:
        """What the daily limit counts: today's payouts to other people, from write-ahead records, so a payment
        still in flight (or unknown) already counts. Failed and returned ones don't."""
        today = time.strftime("%Y-%m-%d")
        return sum(r["amount_cents"] for r in self.payments_for(user_id)
                   if r["kind"] == "transfer" and time.strftime("%Y-%m-%d", time.localtime(r["created_at"])) == today
                   and r["state"] not in payments.RELEASES)

    def payee_history(self, user_id: str, payee_id: str) -> list[int]:
        """Past amounts paid to this payee (positive, in cents). Used by the risk rules; never shown to the model."""
        return [-t["amount_cents"] for t in self.user(user_id)["transactions"]
                if t["payee_id"] == payee_id and t["amount_cents"] < 0]

    def payee_sent_total(self, user_id: str, payee_id: str, exclude_draft: str = "") -> int:
        """Everything on its way to or received by this payee: signed records not given back (in flight counts), plus any
        older history with no record behind it."""
        with self._lock:
            recorded = sum(r["amount_cents"] for r in self.payments.values()
                           if r["user_id"] == user_id and r["card"].get("payee_id") == payee_id and r["kind"] == "transfer"
                           and r["draft_id"] != exclude_draft and r["state"] not in payments.RELEASES)
            older = sum(-t["amount_cents"] for t in self.user(user_id)["transactions"]
                        if t["payee_id"] == payee_id and t["amount_cents"] < 0 and not t.get("payment_id"))
            return recorded + older

    def protection(self, user_id: str) -> dict:
        return self.user(user_id)["protection"]

    @atomic_write
    def update_protection(self, user_id: str, level=None, pending=None):
        p = self.protection(user_id)
        if level is not None:
            p["level"] = level
        p["pending"] = pending
        return copy.deepcopy(p)

    def recent_exec_count(self, user_id: str, window_sec: int, plan_id: str = "") -> int:
        """Payments executed in the window. With plan_id: not counting earlier steps of that same request (the
        customer said all the steps in one sentence and signed each one; a plan has at most 5 steps)."""
        now = time.time()
        n = sum(1 for t in self.recent_exec_times.get(user_id, []) if now - t <= window_sec)
        if plan_id:
            n -= sum(1 for r in self.payments.values() if r.get("user_id") == user_id
                     and now - r.get("created_at", 0) <= window_sec and r["card"].get("plan_id") == plan_id)
        return max(0, n)

    def _check_velocity(self, user_id, card: dict | None = None):
        # Called inside the atomic ledger lock: two signed requests cannot both take the last slot.
        plan_id = (card or {}).get("plan_id", "")
        if self.recent_exec_count(user_id, settings.velocity_window_min * 60, plan_id=plan_id) >= settings.velocity_max:
            raise ValueError("R21_VELOCITY: transaction frequency limit reached; wait and prepare a new request")

    # ---------- Payment records ----------
    def payment(self, payment_id: str) -> dict | None:
        with self._lock:
            return self.payments.get(payment_id)

    def payment_by_draft(self, user_id: str, draft_id: str) -> dict | None:
        with self._lock:
            return next((r for r in self.payments.values() if r["user_id"] == user_id and r["draft_id"] == draft_id), None)

    def payment_by_key(self, idempotency_key: str) -> dict | None:
        with self._lock:
            return next((r for r in self.payments.values() if r["idempotency_key"] == idempotency_key), None)

    def payments_for(self, user_id: str) -> list[dict]:
        with self._lock:
            return sorted((r for r in self.payments.values() if r["user_id"] == user_id), key=lambda r: r["created_at"], reverse=True)

    def open_unknown(self, user_id: str) -> list[dict]:
        return [r for r in self.payments_for(user_id) if r["state"] == "UNKNOWN"]

    def transaction(self, user_id: str, draft_id: str) -> dict | None:
        """A signed earlier step, for dependency checks. status comes from verified state only."""
        with self._lock:
            r = self.payment_by_draft(user_id, draft_id)
            return {**copy.deepcopy(r), "status": payments.status_of(r)} if r else None

    def open_bill_payment(self, user_id: str, invoice_id: str, exclude_draft: str = "") -> dict | None:
        """A signed payment already on this invoice that hasn't been given back."""
        with self._lock:
            return next((r for r in self.payments.values() if r["user_id"] == user_id and r["kind"] == "bill"
                         and r["card"].get("invoice_id") == invoice_id and r["draft_id"] != exclude_draft
                         and r["state"] not in payments.RELEASES), None)

    def _check_invoice(self, rec: dict) -> None:
        invoice_id = rec["card"].get("invoice_id")
        inv = self.invoice(rec["user_id"], invoice_id or "")
        if not inv:
            return
        if inv["status"] not in ("SENT", "UNPAID"):
            raise ValueError(f"R62_BILL_IN_PROGRESS: this bill is already {inv['status'].lower()}")
        if inv["amount_cents"] != rec["amount_cents"]:
            raise ValueError("R62_BILL_IN_PROGRESS: the amount isn't the amount on this bill")
        if self.open_bill_payment(rec["user_id"], invoice_id, rec["draft_id"]):
            raise ValueError("R62_BILL_IN_PROGRESS: a payment on this bill is already under way")

    # ---------- Write: only the execution gateway, reconcile and webhooks may call these ----------
    @atomic_write
    def begin_payment(self, rec: dict, *, reserve: bool) -> dict:
        """Write-ahead (I3): checked and saved under the ledger lock, before any HTTP request goes out."""
        with self._lock:
            user, cents = rec["user_id"], rec["amount_cents"]
            if type(cents) is not int or cents <= 0:
                raise ValueError("Amount must be a positive integer number of cents")
            if self.user(user)["frozen"]["on"] and rec["kind"] != "refund":     # money coming back is never frozen
                raise ValueError("R14_WALLET_FROZEN: payments are frozen")
            if reserve:                     # only spending counts: getting money back is never rate limited
                self._check_velocity(user, rec["card"])
            if rec["kind"] == "bill":       # one bill, one payment: checked under the same lock as the reservation
                self._check_invoice(rec)
            if reserve:
                acct = self.accounts(user)[0]
                if acct["balance_cents"] < cents:
                    raise ValueError("Over the Star allowance")
                acct["balance_cents"] -= cents
            self.payments[rec["payment_id"]] = rec
            if reserve:
                self.recent_exec_times.setdefault(user, []).append(time.time())
            for event in ("gateway.posted", "commit.executed"):
                self.outbox.append({"event": event, "session_id": rec["session_id"], "data": {
                    "draft_id": rec["draft_id"], "txn_id": rec["payment_id"], "digest": rec["digest"], "kind": rec["kind"],
                    "rail": rec["rail"], "state": rec["state"], "idempotency_key": rec["idempotency_key"],
                    "card": payments.masked_card(rec["card"])}})
            return rec

    @atomic_write
    def update_payment(self, payment_id: str, to: str | None = None, *, paypal: dict | None = None, verified_by: str = "",
                       source: str = "", event_type: str = "", note: str = "", event_id: str = "") -> dict:
        """One atomic step of a record: its PayPal ids, its state, and the side effects that state brings."""
        with self._lock:
            rec = self.payments[payment_id]
            for k, v in (paypal or {}).items():
                if v:
                    rec["paypal"][k] = v
            if event_id and event_id not in rec["event_ids"]:
                rec["event_ids"].append(event_id)
            if to is None:
                return rec
            before = rec["state"]
            if not payments.apply(rec, to, verified_by=verified_by, source=source, event_type=event_type, note=note):
                return rec
            self._on_state(rec, before, to)
            self.outbox.append({"event": "payment.state", "session_id": rec["session_id"], "data": {
                "payment_id": payment_id, "from": before, "to": to, "verified_by": verified_by,
                "source": source, "event_type": event_type}})
            return rec

    def _on_state(self, rec: dict, before: str, to: str) -> None:
        user = rec["user_id"]
        if to in payments.RELEASES and not rec["released"] and rec["kind"] in payments.SPENDS:
            rec["released"] = True
            self.accounts(user)[0]["balance_cents"] += rec["amount_cents"]
        if rec["kind"] == "bill" and rec["card"].get("invoice_id"):
            inv = self.invoice(user, rec["card"]["invoice_id"])
            if inv and to in ("PAID", "INVOICE_CANCELLED"):
                inv["status"] = "PAID" if to == "PAID" else "CANCELLED"
        if to in ("SENT", "PAID") and rec.get("verified") and rec["kind"] in payments.SPENDS:
            self.user(user)["transactions"].insert(0, {
                "date": time.strftime("%Y-%m-%d"), "desc": rec["counterparty_name"] if rec["kind"] != "transfer"
                else f"Transfer to {rec['counterparty_name']}", "amount_cents": -rec["amount_cents"],
                "payee_id": rec["card"].get("payee_id"), "payment_id": rec["payment_id"]})
        if to == "PAID" and rec["kind"] == "purchase":
            user_world = self.user(user)
            user_world.setdefault("merchant_orders", {})
            mid = rec["card"].get("merchant_id") or ""
            user_world["merchant_orders"][mid] = user_world["merchant_orders"].get(mid, 0) + 1
        if rec["kind"] == "refund" and to == "REFUNDED":
            self._money_back(rec)
        if rec["kind"] == "money_request" and to == "PAID":
            self.user(user)["transactions"].insert(0, {
                "date": time.strftime("%Y-%m-%d"), "desc": f"Paid back by {rec['counterparty_name']}",
                "amount_cents": rec["amount_cents"], "payee_id": None, "payment_id": rec["payment_id"]})

    def _money_back(self, rec: dict) -> None:
        """A verified refund or returned payout: the parent's allowance comes back, and its record says so."""
        parent = self.payments.get(rec.get("parent_payment_id") or "")
        if not parent:
            return
        parent["refunded_cents"] = parent.get("refunded_cents", 0) + rec["amount_cents"]
        self.accounts(parent["user_id"])[0]["balance_cents"] += rec["amount_cents"]
        self.user(parent["user_id"])["transactions"].insert(0, {
            "date": time.strftime("%Y-%m-%d"), "desc": f"Refund from {parent['counterparty_name']}",
            "amount_cents": rec["amount_cents"], "payee_id": None, "payment_id": rec["payment_id"]})
        if parent["rail"] == "payouts" and parent["state"] in ("UNCLAIMED", "SENT"):
            payments.apply(parent, "RETURNED", verified_by=rec.get("verified_by", ""), source="refund record")
            parent["released"] = True
        elif parent["refunded_cents"] >= parent["amount_cents"] and parent["state"] == "PAID":
            payments.apply(parent, "REFUNDED", verified_by=rec.get("verified_by", ""), source="refund record")

    @atomic_write
    def note_webhook_event(self, event_id: str, event_type: str) -> bool:
        """Insert-or-ignore. True the first time this id is seen."""
        with self._lock:
            if event_id in self.webhook_events:
                return False
            self.webhook_events[event_id] = {"type": event_type, "received_at": time.time()}
            if len(self.webhook_events) > 20000:
                self.webhook_events.pop(next(iter(self.webhook_events)))
            return True

    @atomic_write
    def process_webhook(self, event_id: str, event_type: str, apply):
        """Persist dedup and all effects in one snapshot. An exception leaves the event available for redelivery."""
        if event_id in self.webhook_events:
            return None
        result = apply()
        self.note_webhook_event(event_id, event_type)
        return result

    # ---------- Protective and signed account changes (called by the kernel only) ----------
    @atomic_write
    def add_payee(self, user_id: str, name: str, email: str, relation: str) -> dict:
        with self._lock:
            p = {"payee_id": "p_" + uuid.uuid4().hex[:6], "name": name, "relation": relation or "payee",
                 "type": "person", "aliases": [name.lower(), *([relation.lower()] if relation else [])],
                 "paypal_email": email, "masked_account": payments.mask_email(email), "added_at": time.time()}
            self.user(user_id)["payees"].append(p)
            return dict(p)

    @atomic_write
    def set_daily_limit(self, user_id: str, cents: int, effective_at: float | None = None) -> dict:
        with self._lock:
            lim = self.user(user_id)["limits"]
            if effective_at is None:
                lim["daily_transfer_cents"], lim["pending"] = cents, None
            else:
                lim["pending"] = {"to_cents": cents, "effective_at": effective_at}
            return dict(lim)

    @atomic_write
    def add_alert(self, user_id: str, level: str, kind: str, text: str, draft_id: str = "") -> dict:
        with self._lock:
            alert = {"alert_id": "al_" + uuid.uuid4().hex[:8], "at": time.time(), "level": level, "kind": kind,
                     "text": text, "draft_id": draft_id, "read": False}
            rows = self.user(user_id)["alerts"]
            rows.insert(0, alert)
            del rows[100:]
            return dict(alert)

    @atomic_write
    def add_stop(self, user_id: str, *, kind: str, to: str, amount_cents: int, status: str, stopped_by: str, why: str,
                 codes: list[str] | None = None, draft_id: str = "", digest: str = "", session_id: str = "") -> dict:
        with self._lock:
            row = {"row_id": "stop_" + uuid.uuid4().hex[:10], "time": time.time(), "kind": kind, "to": to, "amount_cents": amount_cents,
                   "status": status, "stopped_by": stopped_by, "why": why, "codes": codes or [], "draft_id": draft_id,
                   "digest": digest, "session_id": session_id}
            rows = self.user(user_id)["stops"]
            rows.insert(0, row)
            del rows[200:]
            return dict(row)

    def stops(self, user_id: str) -> list[dict]:
        return self.user(user_id)["stops"]

    @atomic_write
    def set_frozen(self, user_id: str, on: bool, by: str) -> dict:
        with self._lock:
            f = self.user(user_id)["frozen"]
            f.update(on=on, by=by if on else "", at=time.time() if on else None, lift={"owner": False, "guardian": False})
            return dict(f)

    @atomic_write
    def mark_lift(self, user_id: str, who: str) -> dict:
        with self._lock:
            f = self.user(user_id)["frozen"]
            f["lift"][who] = True
            return copy.deepcopy(f)

    @atomic_write
    def update_user(self, user_id: str, fn) -> object:
        """A small atomic change to one customer's world (guardian pairing, invoice sync)."""
        with self._lock:
            return fn(self.user(user_id))


wallet = Wallet()
