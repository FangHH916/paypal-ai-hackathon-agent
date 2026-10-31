"""PayPal simulator: an in-process transport that follows PayPal's documented request and response shapes.

It is a requests transport adapter, mounted on the same requests.Session as the real client, so the client code is
identical in both modes (base URL https://paypal-simulator.invalid, which can never resolve). Nothing here is PayPal,
no money of any kind moves, and every status the app derives from it is labelled SIMULATED.
Differences from the real API are listed in docs/PAYPAL_NOTES.md.

Webhooks: a state change queues a signed event. Events are handed by due_events() to the same handler a real webhook
reaches (app/paypal/webhooks.py), and verify-webhook-signature here checks an HMAC under a process-local secret, so a
forged event fails. Real PayPal signatures use certificates; the simulator's are NOT that.

Fault injection (per visitor, simulator only) is what the failure drill and the reconciliation tests use.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import io
import json
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests
from requests.adapters import HTTPAdapter

from app.paypal import calls

BASE = "https://paypal-simulator.invalid"
WEBHOOK_ID = "SIMULATORWEBHOOKID"
FAMILY_DOMAIN = "family.example"       # registered family accept at once; anyone else stays UNCLAIMED until they claim
FAULTS = {"timeout_after_commit", "timeout_before_commit", "http_500", "decline_instrument", "delayed_webhook",
          "duplicate_webhook", "forged_webhook", "amount_mismatch", "capture_pending"}


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iso(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t or time.time()))


def _pid(prefix: str, n: int = 13) -> str:
    return prefix + secrets.token_hex(n)[:n].upper()


class Simulator:
    def __init__(self, store_path=None, settle_seconds: float = 0.0, webhook_delay: float = 0.0):
        self.settle_seconds, self.webhook_delay = settle_seconds, webhook_delay
        self.store_path = store_path
        self.secret = secrets.token_bytes(32)      # process-local: events queued before a restart simply can't verify
        self._lock = threading.RLock()
        self.st = self._fresh()
        self.events: list[dict] = []               # queued for delivery: {due, raw, headers}
        self.faults: dict[str, list[dict]] = {}    # user id -> armed faults
        self.requests_seen: list[dict] = []        # for tests and the drill: what actually reached the simulator
        self._load()

    @staticmethod
    def _fresh() -> dict:
        return {"tokens": {}, "batches": {}, "batch_by_sender": {}, "items": {}, "orders": {}, "order_by_key": {},
                "captures": {}, "capture_by_key": {}, "refunds": {}, "refund_by_key": {}, "invoices": {},
                "payout_by_key": {}, "txn_log": []}

    # ---------------------------------------------------------------- persistence
    def _load(self):
        if not self.store_path:
            return
        Path(self.store_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.store_path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS simulator_state (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
            row = db.execute("SELECT data FROM simulator_state WHERE id=1").fetchone()
        if row:
            self.st = {**self._fresh(), **json.loads(row[0])}

    def _save(self):
        if self.store_path:
            with closing(sqlite3.connect(self.store_path)) as db, db:
                db.execute("INSERT OR REPLACE INTO simulator_state VALUES (1, ?)", (json.dumps(self.st),))

    # ---------------------------------------------------------------- faults
    def arm(self, user_id: str, fault: str, *, op: str = "", count: int = 1) -> None:
        if fault not in FAULTS:
            raise ValueError(f"Unknown fault: {fault}")
        with self._lock:
            self.faults.setdefault(user_id, []).append({"fault": fault, "op": op, "count": count})

    def disarm(self, user_id: str) -> None:
        with self._lock:
            self.faults.pop(user_id, None)

    def _take_fault(self, names: set[str], op: str) -> str:
        user = calls.current().user_id
        with self._lock:
            for key in (user, "*"):
                for f in self.faults.get(key, []):
                    if f["fault"] in names and f["count"] > 0 and (not f["op"] or f["op"] == op):
                        f["count"] -= 1
                        return f["fault"]
        return ""

    # ---------------------------------------------------------------- the other people's side (the simulator's pages)
    def buyer_decide_order(self, order_id: str, decision: str) -> str:
        """The simulated buyer approves or cancels. Returns the merchant's return or cancel URL. Nothing is captured."""
        with self._lock:
            o = self.st["orders"].get(order_id)
            if not o:
                raise KeyError(order_id)
            ctx = o["_ctx"]
            if decision == "approve":
                if o["status"] in ("PAYER_ACTION_REQUIRED", "CREATED"):
                    o["status"] = "APPROVED"
                    o["payer"] = {"email_address": "buyer@personal.example", "payer_id": _pid("SIMPAYER", 5),
                                  "name": {"given_name": "Demo", "surname": "Buyer"}}
                    self._emit("CHECKOUT.ORDER.APPROVED", "checkout-order", self._public(o), "An order has been approved.")
                self._save()
                return ctx["return_url"]
            self._save()
            return ctx["cancel_url"]

    def payer_pay_invoice(self, invoice_id: str) -> None:
        with self._lock:
            inv = self.st["invoices"].get(invoice_id)
            if not inv:
                raise KeyError(invoice_id)
            if inv["status"] in ("SENT", "UNPAID"):
                cur, total = inv["detail"]["currency_code"], inv["amount"]["value"]
                inv["status"] = "PAID"
                inv["due_amount"] = {"currency_code": cur, "value": "0.00"}
                inv["payments"] = {"paid_amount": {"currency_code": cur, "value": total},
                                   "transactions": [{"payment_id": _pid("SIMPAY"), "method": "PAYPAL",
                                                     "amount": {"currency_code": cur, "value": total}}]}
                self._emit("INVOICING.INVOICE.PAID", "invoices", {"invoice": copy.deepcopy(inv)}, "An invoice was paid.")
                self._txn("T0007", 1, total, cur, inv["id"])
            self._save()

    def claim_payout_item(self, item_id: str) -> None:
        """The simulated receiver opens an account and claims an UNCLAIMED payout."""
        with self._lock:
            it = self.st["items"].get(item_id)
            if it and it["transaction_status"] == "UNCLAIMED":
                it["transaction_status"] = "SUCCESS"
                it["transaction_id"] = _pid("SIMTXN")
                self._emit("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", "payouts_item", self._item_view(it), "A payout item has succeeded.")
                self._save()

    def seed_invoice(self, *, number: str, invoicer_name: str, invoicer_email: str, item_name: str, amount_cents: int,
                     currency: str, note: str, terms: str, due: str, reference: str) -> dict:
        """What an outside biller's system would have done. Used by the world seeds and the new-invoice control."""
        with self._lock:
            existing = next((i for i in self.st["invoices"].values()
                             if i["detail"].get("invoice_number") == number and i["detail"].get("reference") == reference), None)
            if existing:
                return copy.deepcopy(existing)
            val = f"{amount_cents // 100}.{amount_cents % 100:02d}"
            inv_id = self._invoice_id()
            inv = {"id": inv_id, "status": "SENT", "detail": {
                       "invoice_number": number, "reference": reference, "currency_code": currency, "note": note,
                       "terms_and_conditions": terms, "invoice_date": _iso()[:10],
                       "payment_term": {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": due},
                       "metadata": {"create_time": _iso(), "recipient_view_url": f"{BASE}/simulator/invoice?id={inv_id}"}},
                   "invoicer": {"business_name": invoicer_name, "email_address": invoicer_email},
                   "primary_recipients": [{"billing_info": {"email_address": "buyer@personal.example"}}],
                   "items": [{"name": item_name, "quantity": "1", "unit_amount": {"currency_code": currency, "value": val}}],
                   "amount": {"currency_code": currency, "value": val}, "due_amount": {"currency_code": currency, "value": val},
                   "links": [{"href": f"{BASE}/v2/invoicing/invoices/{inv_id}", "rel": "self", "method": "GET"}]}
            self.st["invoices"][inv_id] = inv
            self._save()
            return copy.deepcopy(inv)

    # ---------------------------------------------------------------- webhook queue
    def _emit(self, event_type: str, resource_type: str, resource: dict, summary: str) -> None:
        event = {"id": "WH-" + secrets.token_hex(10).upper(), "event_version": "1.0", "create_time": _iso(),
                 "resource_type": resource_type, "event_type": event_type, "summary": summary,
                 "resource": copy.deepcopy(resource), "links": []}
        raw = _canon(event).encode()
        due = time.time() + self.webhook_delay + (5.0 if self._take_fault({"delayed_webhook"}, "") else 0.0)
        self.events.append({"due": due, "raw": raw, "headers": self._sign(event)})
        if self._take_fault({"duplicate_webhook"}, ""):
            self.events.append({"due": due, "raw": raw, "headers": self._sign(event)})
        if self._take_fault({"forged_webhook"}, ""):
            forged = copy.deepcopy(event)
            forged["id"] = "WH-" + secrets.token_hex(10).upper()
            self.events.append({"due": due, "raw": _canon(forged).encode(),
                                "headers": {**self._sign(event), "paypal-transmission-sig": "Zm9yZ2Vk"}})

    def _sign(self, event: dict) -> dict:
        tid, when = "SIM" + secrets.token_hex(12), _iso()
        return {"paypal-transmission-id": tid, "paypal-transmission-time": when,
                "paypal-transmission-sig": self._hmac(tid, when, event), "paypal-cert-url": f"{BASE}/simulator/cert",
                "paypal-auth-algo": "SIMHMACSHA256"}

    def _hmac(self, tid: str, when: str, event: dict) -> str:
        msg = f"{tid}|{when}|{WEBHOOK_ID}|{hashlib.sha256(_canon(event).encode()).hexdigest()}"
        return base64.b64encode(hmac.new(self.secret, msg.encode(), hashlib.sha256).digest()).decode()

    def due_events(self) -> list[dict]:
        with self._lock:
            self._settle(time.time())
            now = time.time()               # after settling: what it just emitted counts as due
            ready = [e for e in self.events if e["due"] <= now]
            self.events = [e for e in self.events if e["due"] > now]
            return ready

    # ---------------------------------------------------------------- settle: time passes
    def _settle(self, now: float) -> None:
        for b in self.st["batches"].values():
            if b["batch_header"]["batch_status"] in ("PENDING", "PROCESSING") and b["_settle_at"] <= now:
                b["batch_header"]["batch_status"] = "SUCCESS"
                b["batch_header"]["time_completed"] = _iso(now)
                self._emit("PAYMENT.PAYOUTSBATCH.SUCCESS", "payouts", {"batch_header": b["batch_header"]},
                           "Payouts batch completed successfully.")
                for iid in b["_items"]:
                    it = self.st["items"][iid]
                    if it["payout_item"]["receiver"].endswith("@" + FAMILY_DOMAIN):
                        it["transaction_status"] = "SUCCESS"
                        it["transaction_id"] = _pid("SIMTXN")
                        amt = it["payout_item"]["amount"]
                        self._txn("T0000", -1, amt["value"], amt["currency"], it["transaction_id"])
                        self._emit("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", "payouts_item", self._item_view(it), "A payout item has succeeded.")
                    else:
                        it["transaction_status"] = "UNCLAIMED"
                        self._emit("PAYMENT.PAYOUTS-ITEM.UNCLAIMED", "payouts_item", self._item_view(it), "A payout item is unclaimed.")
                self._save()
        for c in self.st["captures"].values():
            if c["status"] == "PENDING" and c.get("_settle_at", 0) <= now:
                c["status"] = "COMPLETED"
                c.pop("status_details", None)
                self._sync_capture(c)
                self._emit("PAYMENT.CAPTURE.COMPLETED", "capture", self._public(c), "Payment completed.")
                self._save()

    def _txn(self, code: str, sign: int, val: str, cur: str, ref: str) -> None:
        self.st["txn_log"].append({"transaction_info": {
            "transaction_id": ref, "transaction_event_code": code,
            "transaction_initiation_date": _iso(), "transaction_updated_date": _iso(),
            "transaction_amount": {"currency_code": cur, "value": ("-" if sign < 0 else "") + val},
            "transaction_status": "S"}})

    @staticmethod
    def _invoice_id() -> str:
        c = secrets.token_hex(8).upper()
        return f"INV2-{c[:4]}-{c[4:8]}-{c[8:12]}-{c[12:16]}"

    @staticmethod
    def _public(obj: dict) -> dict:
        return {k: v for k, v in obj.items() if not k.startswith("_")}

    def _item_view(self, it: dict) -> dict:
        return copy.deepcopy(self._public(it))

    # ---------------------------------------------------------------- HTTP-level dispatch
    def handle(self, request) -> requests.Response:
        with self._lock:
            self._settle(time.time())
            u = urlsplit(request.url)
            path, query = u.path, {k: v[-1] for k, v in parse_qs(u.query).items()}
            raw = request.body or b""
            raw = raw.encode() if isinstance(raw, str) else raw
            h = {k.lower(): v for k, v in request.headers.items()}
            self.requests_seen.append({"method": request.method, "path": path, "body": raw, "headers": {
                k: v for k, v in h.items() if k != "authorization"}})
            if path == "/v1/oauth2/token":
                return self._oauth(request, h, raw)
            token = (h.get("authorization") or "").removeprefix("Bearer ").strip()
            exp = self.st["tokens"].get(token)
            if not exp or exp < time.time():
                return self._json(request, 401, {"error": "invalid_token", "error_description": "Token signature verification failed"})
            for method, rx, fn, op in self._ROUTES:
                m = rx.fullmatch(path)
                if m and method == request.method:
                    return self._dispatch(request, fn, op, m, h, raw, query)
            return self._error(request, 404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.")

    def _dispatch(self, request, fn, op, m, h, raw, query):
        write = request.method == "POST" and op not in ("invoice_search", "webhook_verify")
        if write:
            fault = self._take_fault({"timeout_before_commit", "http_500"}, op)
            if fault == "timeout_before_commit":
                raise requests.exceptions.ReadTimeout("simulated timeout before commit")
            if fault == "http_500":
                return self._error(request, 500, "INTERNAL_SERVER_ERROR", "An internal server error occurred.")
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            return self._error(request, 400, "MALFORMED_REQUEST", "Malformed JSON body.")
        resp = fn(self, request, m, h, body, query)
        if write and resp.status_code < 400:
            self._save()
            if self._take_fault({"timeout_after_commit"}, op):
                raise requests.exceptions.ReadTimeout("simulated timeout after commit: processed, reply lost")
        return resp

    def _oauth(self, request, h, raw):
        if not (h.get("authorization") or "").startswith("Basic "):
            return self._json(request, 401, {"error": "invalid_client", "error_description": "Client Authentication failed"})
        if b"grant_type=client_credentials" not in raw:
            return self._json(request, 400, {"error": "unsupported_grant_type", "error_description": "Grant Type is NULL"})
        token = "SIM-" + secrets.token_urlsafe(24)
        self.st["tokens"] = {t: e for t, e in self.st["tokens"].items() if e > time.time()}
        self.st["tokens"][token] = time.time() + 32400
        return self._json(request, 200, {"scope": "simulator", "access_token": token, "token_type": "Bearer",
                                         "app_id": "APP-SIMULATOR", "expires_in": 32400, "nonce": secrets.token_hex(8)})

    # ---------------------------------------------------------------- responses
    @staticmethod
    def _json(request, status: int, body: dict | list | None, headers: dict | None = None) -> requests.Response:
        r = requests.Response()
        r.status_code = status
        data = json.dumps(body).encode() if body is not None else b""
        r.raw, r._content, r._content_consumed = io.BytesIO(data), data, True
        r.url, r.request, r.encoding = request.url, request, "utf-8"
        r.headers.update({"Content-Type": "application/json", "paypal-debug-id": secrets.token_hex(7), **(headers or {})})
        return r

    def _error(self, request, status: int, name: str, message: str, issue: str = "", links: list | None = None):
        body = {"name": name, "message": message, "debug_id": secrets.token_hex(7)}
        if issue:
            body["details"] = [{"issue": issue, "description": message}]
        if links:
            body["links"] = links
        return self._json(request, status, body)

    def _missing(self, request):
        return self._error(request, 404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.")

    # ---------------------------------------------------------------- Payouts
    def _payout_create(self, request, m, h, body, query):
        hdr, items = body.get("sender_batch_header") or {}, body.get("items") or []
        if "sender_batch_id" not in hdr or not items:
            return self._error(request, 400, "VALIDATION_ERROR", "sender_batch_header and items are required.")
        for it in items:
            if not it.get("receiver") or not isinstance(it.get("amount"), dict):
                return self._error(request, 400, "VALIDATION_ERROR", "Each item needs receiver and amount.")
        sender, key = hdr["sender_batch_id"], h.get("paypal-request-id")
        if sender in self.st["batch_by_sender"]:
            bid = self.st["batch_by_sender"][sender]
            if key and self.st["payout_by_key"].get(key) == bid:
                return self._json(request, 201, self._batch_view(bid, full=False))
            # Spec: a duplicate id is rejected, with a HATEOAS link to the original. The exact JSON shape is not documented.
            return self._error(request, 400, "USER_BUSINESS_ERROR",
                               f"Batch with given sender_batch_id already exists: {sender}",
                               links=[{"href": f"{BASE}/v1/payments/payouts/{bid}", "rel": "self", "method": "GET"}])
        bid = _pid("SIMBATCH", 10)
        now = time.time()
        total = sum(float(i["amount"]["value"]) for i in items)
        batch = {"batch_header": {"payout_batch_id": bid, "batch_status": "PENDING", "time_created": _iso(now),
                                  "sender_batch_header": {k: hdr[k] for k in ("sender_batch_id", "email_subject", "email_message") if k in hdr},
                                  "amount": {"currency": items[0]["amount"]["currency"], "value": f"{total:.2f}"}},
                 "_items": [], "_settle_at": now + self.settle_seconds}
        for it in items:
            iid = _pid("SIMITEM", 10)
            batch["_items"].append(iid)
            self.st["items"][iid] = {"payout_item_id": iid, "payout_batch_id": bid, "transaction_status": "PENDING",
                                     "sender_batch_id": sender,
                                     "payout_item": {k: it[k] for k in ("recipient_type", "amount", "note", "receiver", "sender_item_id") if k in it},
                                     "links": [{"href": f"{BASE}/v1/payments/payouts-item/{iid}", "rel": "self", "method": "GET"}]}
        self.st["batches"][bid] = batch
        self.st["batch_by_sender"][sender] = bid
        if key:
            self.st["payout_by_key"][key] = bid
        return self._json(request, 201, self._batch_view(bid, full=False))

    def _batch_view(self, bid: str, full: bool = True) -> dict:
        b = self.st["batches"][bid]
        links = [{"href": f"{BASE}/v1/payments/payouts/{bid}", "rel": "self", "method": "GET"}]
        if not full:
            hdr = b["batch_header"]
            return {"batch_header": {k: hdr[k] for k in ("payout_batch_id", "batch_status", "sender_batch_header")}, "links": links}
        # The spec's batch items have no sender_batch_id; only GET payouts-item has it
        items = [{k: v for k, v in self._item_view(self.st["items"][i]).items() if k != "sender_batch_id"} for i in b["_items"]]
        return {"batch_header": copy.deepcopy(b["batch_header"]), "items": items,
                "total_items": len(b["_items"]), "total_pages": 1, "links": links}

    def _payout_get(self, request, m, h, body, query):
        return self._json(request, 200, self._batch_view(m["id"])) if m["id"] in self.st["batches"] else self._missing(request)

    def _item_get(self, request, m, h, body, query):
        it = self.st["items"].get(m["id"])
        return self._json(request, 200, self._item_view(it)) if it else self._missing(request)

    def _item_cancel(self, request, m, h, body, query):
        it = self.st["items"].get(m["id"])
        if not it:
            return self._missing(request)
        if it["transaction_status"] != "UNCLAIMED":
            return self._error(request, 400, "VALIDATION_ERROR", "Only an unclaimed payout item can be cancelled.")
        it["transaction_status"] = "RETURNED"
        self._emit("PAYMENT.PAYOUTS-ITEM.CANCELED", "payouts_item", self._item_view(it), "A payout item was canceled.")
        return self._json(request, 200, self._item_view(it))

    # ---------------------------------------------------------------- Orders
    def _order_create(self, request, m, h, body, query):
        key = h.get("paypal-request-id")
        if key and key in self.st["order_by_key"]:
            return self._json(request, 200, self._public(self.st["orders"][self.st["order_by_key"][key]]))
        units = body.get("purchase_units") or []
        if body.get("intent") not in ("CAPTURE", "AUTHORIZE") or not units or "amount" not in units[0]:
            return self._error(request, 400, "INVALID_REQUEST", "Request is not well-formed.")
        ctx = ((body.get("payment_source") or {}).get("paypal") or {}).get("experience_context") or {}
        oid = _pid("SIMORD", 13)
        rel, status = ("payer-action", "PAYER_ACTION_REQUIRED") if body.get("payment_source") else ("approve", "CREATED")
        o = {"id": oid, "intent": body["intent"], "status": status, "create_time": _iso(),
             "purchase_units": copy.deepcopy(units),
             "links": [{"href": f"{BASE}/v2/checkout/orders/{oid}", "rel": "self", "method": "GET"},
                       {"href": f"{BASE}/simulator/checkout?token={oid}", "rel": rel, "method": "GET"}],
             "_ctx": {"return_url": ctx.get("return_url", ""), "cancel_url": ctx.get("cancel_url", "")}}
        self.st["orders"][oid] = o
        if key:
            self.st["order_by_key"][key] = oid
        return self._json(request, 201, self._public(o))

    def _order_get(self, request, m, h, body, query):
        o = self.st["orders"].get(m["id"])
        return self._json(request, 200, self._public(o)) if o else self._missing(request)

    def _order_capture(self, request, m, h, body, query):
        o = self.st["orders"].get(m["id"])
        if not o:
            return self._missing(request)
        key = h.get("paypal-request-id")
        if key and key in self.st["capture_by_key"]:
            return self._json(request, 200, self._public(o))
        if o["status"] == "COMPLETED":
            return self._error(request, 422, "UNPROCESSABLE_ENTITY", "Order already captured.", issue="ORDER_ALREADY_CAPTURED")
        if o["status"] != "APPROVED":
            return self._error(request, 422, "UNPROCESSABLE_ENTITY", "Payer has not approved the order.", issue="ORDER_NOT_APPROVED")
        if self._take_fault({"decline_instrument"}, "order_capture"):
            return self._error(request, 422, "UNPROCESSABLE_ENTITY", "The instrument presented was declined.", issue="INSTRUMENT_DECLINED")
        unit = o["purchase_units"][0]
        amount = {k: v for k, v in unit["amount"].items() if k != "breakdown"}
        if self._take_fault({"amount_mismatch"}, "order_capture"):
            amount["value"] = f"{float(amount['value']) + 1:.2f}"
        pending = self._take_fault({"capture_pending"}, "order_capture")
        cid = _pid("SIMCAP", 13)
        cap = {"id": cid, "status": "PENDING" if pending else "COMPLETED", "amount": amount, "final_capture": True,
               "custom_id": unit.get("custom_id", ""), "invoice_id": unit.get("invoice_id", ""),
               "create_time": _iso(), "update_time": _iso(),
               "links": [{"href": f"{BASE}/v2/payments/captures/{cid}", "rel": "self", "method": "GET"},
                         {"href": f"{BASE}/v2/payments/captures/{cid}/refund", "rel": "refund", "method": "POST"},
                         {"href": f"{BASE}/v2/checkout/orders/{o['id']}", "rel": "up", "method": "GET"}],
               "supplementary_data": {"related_ids": {"order_id": o["id"]}},
               "_order": o["id"], "_refunded": 0.0, "_settle_at": time.time() + (2.0 if pending else 0)}
        if pending:
            cap["status_details"] = {"reason": "PENDING_REVIEW"}
        self.st["captures"][cid] = cap
        o["status"] = "COMPLETED"
        unit["payments"] = {"captures": [self._in_order(cap)]}
        if key:
            self.st["capture_by_key"][key] = cid
        self._emit("PAYMENT.CAPTURE.PENDING" if pending else "PAYMENT.CAPTURE.COMPLETED", "capture", self._public(cap),
                   "Payment state changed.")
        if not pending:
            self._txn("T0006", 1, amount["value"], amount["currency_code"], cid)
        return self._json(request, 201, self._public(o))

    def _in_order(self, cap: dict) -> dict:
        """A capture as the order shows it. related_ids appear on the webhook resource, not inside an order (spec)."""
        return {k: v for k, v in self._public(cap).items() if k != "supplementary_data"}

    def _sync_capture(self, cap: dict) -> None:
        o = self.st["orders"].get(cap.get("_order", ""))
        if o:
            o["purchase_units"][0]["payments"]["captures"] = [self._in_order(cap)]

    def _capture_get(self, request, m, h, body, query):
        c = self.st["captures"].get(m["id"])
        return self._json(request, 200, self._public(c)) if c else self._missing(request)

    def _capture_refund(self, request, m, h, body, query):
        c = self.st["captures"].get(m["id"])
        if not c:
            return self._missing(request)
        key = h.get("paypal-request-id")
        if key and key in self.st["refund_by_key"]:
            return self._json(request, 200, self._public(self.st["refunds"][self.st["refund_by_key"][key]]))
        if c["status"] not in ("COMPLETED", "PARTIALLY_REFUNDED"):
            return self._error(request, 422, "UNPROCESSABLE_ENTITY", "Capture can't be refunded in this state.")
        total, already = float(c["amount"]["value"]), float(c["_refunded"])
        want = float(body["amount"]["value"]) if body.get("amount") else round(total - already, 2)
        if want <= 0 or want > round(total - already, 2):
            return self._error(request, 422, "UNPROCESSABLE_ENTITY", "Refund amount exceeds what is left on the capture.")
        rid = _pid("SIMREF", 13)
        ref = {"id": rid, "status": "COMPLETED", "amount": {"currency_code": c["amount"]["currency_code"], "value": f"{want:.2f}"},
               "custom_id": c.get("custom_id", ""), "invoice_id": c.get("invoice_id", ""), "create_time": _iso(), "update_time": _iso(),
               "links": [{"href": f"{BASE}/v2/payments/refunds/{rid}", "rel": "self", "method": "GET"},
                         {"href": f"{BASE}/v2/payments/captures/{c['id']}", "rel": "up", "method": "GET"}]}
        if body.get("note_to_payer"):
            ref["note_to_payer"] = body["note_to_payer"]
        self.st["refunds"][rid] = ref
        c["_refunded"] = round(already + want, 2)
        c["status"] = "REFUNDED" if c["_refunded"] >= total else "PARTIALLY_REFUNDED"
        self._sync_capture(c)
        if key:
            self.st["refund_by_key"][key] = rid
        self._emit("PAYMENT.CAPTURE.REFUNDED", "refund", self._public(ref), "A payment capture was refunded.")
        self._txn("T1107", -1, f"{want:.2f}", c["amount"]["currency_code"], rid)
        return self._json(request, 201, self._public(ref))

    def _refund_get(self, request, m, h, body, query):
        r = self.st["refunds"].get(m["id"])
        return self._json(request, 200, self._public(r)) if r else self._missing(request)

    # ---------------------------------------------------------------- Invoicing
    def _invoice_create(self, request, m, h, body, query):
        detail = body.get("detail") or {}
        if not detail.get("currency_code"):
            return self._error(request, 400, "INVALID_REQUEST", "detail.currency_code is required.")
        number = detail.get("invoice_number")
        if number and any(i["detail"].get("invoice_number") == number for i in self.st["invoices"].values()):
            return self._error(request, 422, "UNPROCESSABLE_ENTITY", "Invoice number already exists.")
        inv_id = self._invoice_id()
        items = body.get("items") or []
        total = sum(float(i["unit_amount"]["value"]) * float(i.get("quantity", 1)) for i in items)
        cur = detail["currency_code"]
        inv = {"id": inv_id, "status": "DRAFT",
               "detail": {**detail, "invoice_date": detail.get("invoice_date") or _iso()[:10], "metadata": {"create_time": _iso()}},
               "invoicer": body.get("invoicer") or {"business_name": "Household"},
               "primary_recipients": body.get("primary_recipients") or [], "items": items,
               "amount": {"currency_code": cur, "value": f"{total:.2f}"}, "due_amount": {"currency_code": cur, "value": f"{total:.2f}"},
               "links": [{"href": f"{BASE}/v2/invoicing/invoices/{inv_id}", "rel": "self", "method": "GET"}]}
        self.st["invoices"][inv_id] = inv
        return self._json(request, 201, copy.deepcopy(inv))

    def _invoice_send(self, request, m, h, body, query):
        inv = self.st["invoices"].get(m["id"])
        if not inv:
            return self._missing(request)
        if inv["status"] != "DRAFT":
            return self._error(request, 422, "UNPROCESSABLE_ENTITY", "Invoice can't be sent in this state.")
        inv["status"] = "SENT"
        url = f"{BASE}/simulator/invoice?id={inv['id']}"
        inv["detail"]["metadata"]["recipient_view_url"] = url
        return self._json(request, 200, {"href": url, "rel": "payer-view", "method": "GET"})

    def _invoice_get(self, request, m, h, body, query):
        inv = self.st["invoices"].get(m["id"])
        return self._json(request, 200, copy.deepcopy(inv)) if inv else self._missing(request)

    def _invoice_search(self, request, m, h, body, query):
        rows = list(self.st["invoices"].values())
        for f in ("invoice_number", "reference"):
            if body.get(f):
                rows = [i for i in rows if i["detail"].get(f) == body[f]]
        if body.get("status"):
            rows = [i for i in rows if i["status"] in body["status"]]
        return self._json(request, 200, {"total_items": len(rows), "total_pages": 1, "items": copy.deepcopy(rows), "links": []})

    # ---------------------------------------------------------------- Disputes, Reporting, Webhooks
    _DISPUTES = [{"dispute_id": "PP-D-SIM-0001", "create_time": "2026-10-02T15:04:05.000Z", "update_time": "2026-10-20T10:00:00.000Z",
                  "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "status": "UNDER_REVIEW", "dispute_state": "UNDER_PAYPAL_REVIEW",
                  "dispute_amount": {"currency_code": "USD", "value": "24.00"}, "links": []}]

    def _disputes_list(self, request, m, h, body, query):
        return self._json(request, 200, {"items": copy.deepcopy(self._DISPUTES), "links": []})

    def _dispute_get(self, request, m, h, body, query):
        d = next((x for x in self._DISPUTES if x["dispute_id"] == m["id"]), None)
        if not d:
            return self._missing(request)
        return self._json(request, 200, {k: v for k, v in d.items() if k != "dispute_state"})    # list-only field (spec)

    def _transactions(self, request, m, h, body, query):
        if not query.get("start_date") or not query.get("end_date"):
            return self._error(request, 400, "INVALID_REQUEST", "start_date and end_date are required.")
        rows = self.st["txn_log"][-int(query.get("page_size", 50)):]
        return self._json(request, 200, {"transaction_details": copy.deepcopy(rows), "total_items": len(rows), "total_pages": 1,
                                         "page": 1, "account_number": "SIMULATOR", "start_date": query["start_date"],
                                         "end_date": query["end_date"], "links": []})

    def _webhooks_list(self, request, m, h, body, query):
        return self._json(request, 200, {"webhooks": [{"id": WEBHOOK_ID, "url": f"{BASE}/hook",
                                                       "event_types": [{"name": "*"}], "links": []}]})

    def _verify_signature(self, request, m, h, body, query):
        for f in ("auth_algo", "cert_url", "transmission_id", "transmission_sig", "transmission_time", "webhook_id", "webhook_event"):
            if f not in body:
                return self._error(request, 400, "VALIDATION_ERROR", f"{f} is required.")
        ok = body["webhook_id"] == WEBHOOK_ID and hmac.compare_digest(
            self._hmac(body["transmission_id"], body["transmission_time"], body["webhook_event"]), str(body["transmission_sig"]))
        return self._json(request, 200, {"verification_status": "SUCCESS" if ok else "FAILURE"})

    _ROUTES = [
        ("POST", re.compile(r"/v1/payments/payouts"), _payout_create, "payout_create"),
        ("GET", re.compile(r"/v1/payments/payouts/(?P<id>[^/]+)"), _payout_get, "payout_batch_get"),
        ("GET", re.compile(r"/v1/payments/payouts-item/(?P<id>[^/]+)"), _item_get, "payout_item_get"),
        ("POST", re.compile(r"/v1/payments/payouts-item/(?P<id>[^/]+)/cancel"), _item_cancel, "payout_item_cancel"),
        ("POST", re.compile(r"/v2/checkout/orders"), _order_create, "order_create"),
        ("GET", re.compile(r"/v2/checkout/orders/(?P<id>[^/]+)"), _order_get, "order_get"),
        ("POST", re.compile(r"/v2/checkout/orders/(?P<id>[^/]+)/capture"), _order_capture, "order_capture"),
        ("GET", re.compile(r"/v2/payments/captures/(?P<id>[^/]+)"), _capture_get, "capture_get"),
        ("POST", re.compile(r"/v2/payments/captures/(?P<id>[^/]+)/refund"), _capture_refund, "capture_refund"),
        ("GET", re.compile(r"/v2/payments/refunds/(?P<id>[^/]+)"), _refund_get, "refund_get"),
        ("POST", re.compile(r"/v2/invoicing/invoices"), _invoice_create, "invoice_create"),
        ("GET", re.compile(r"/v2/invoicing/invoices/(?P<id>[^/]+)"), _invoice_get, "invoice_get"),
        ("POST", re.compile(r"/v2/invoicing/invoices/(?P<id>[^/]+)/send"), _invoice_send, "invoice_send"),
        ("POST", re.compile(r"/v2/invoicing/search-invoices"), _invoice_search, "invoice_search"),
        ("GET", re.compile(r"/v1/customer/disputes"), _disputes_list, "disputes_list"),
        ("GET", re.compile(r"/v1/customer/disputes/(?P<id>[^/]+)"), _dispute_get, "dispute_get"),
        ("GET", re.compile(r"/v1/reporting/transactions"), _transactions, "transactions_list"),
        ("GET", re.compile(r"/v1/notifications/webhooks"), _webhooks_list, "webhooks_list"),
        ("POST", re.compile(r"/v1/notifications/verify-webhook-signature"), _verify_signature, "webhook_verify"),
    ]


class SimulatorAdapter(HTTPAdapter):
    def __init__(self, sim: Simulator):
        super().__init__()
        self.sim = sim

    def send(self, request, **kw):
        return self.sim.handle(request)
