"""Exercise every PayPal API Star uses against the REAL sandbox, and say plainly what worked.

    PAYPAL_MODE=sandbox PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... python scripts/paypal_sandbox_check.py \\
        --receiver friend@personal.example --buyer buyer@personal.example

Prints PASS / FAIL / SKIP per API and writes data/sandbox-check.json (ids only, no secrets). Exit code is nonzero on any FAIL.
It creates real SANDBOX objects (test money only). It has NOT been run yet by the authors of this script, as no credentials
were available when it was written: the first run is expected to reveal differences, which is the point. It also prints the
exact JSON of the duplicate-batch rejection, whose shape the spec doesn't document.

Order approval needs a person: it prints a link and waits (--approve-wait seconds; 0 skips that step and what depends on it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.paypal import config, factory, ids  # noqa: E402
from app.paypal.client import payer_link  # noqa: E402
from app.paypal.errors import PayPalError, PayPalUnknown  # noqa: E402

RESULTS: list[dict] = []
EVIDENCE: dict = {}


def step(name: str, fn, skip: str = ""):
    if skip:
        print(f"SKIP  {name}: {skip}")
        RESULTS.append({"api": name, "result": "SKIP", "why": skip})
        return None
    try:
        out = fn()
    except (PayPalError, PayPalUnknown, AssertionError, KeyError, TypeError, ValueError) as e:
        print(f"FAIL  {name}: {e}")
        RESULTS.append({"api": name, "result": "FAIL", "why": str(e)[:300]})
        return None
    print(f"PASS  {name}")
    RESULTS.append({"api": name, "result": "PASS"})
    return out


def _fresh() -> str:
    """A digest for this run only. In the app it comes from the signed card; here it only has to be unique."""
    return hashlib.sha256(secrets.token_bytes(16)).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--receiver", help="a sandbox personal account to receive a payout")
    ap.add_argument("--unclaimed", default="", help="an address with no sandbox account, for the item cancel check")
    ap.add_argument("--buyer", help="the sandbox personal account that approves orders and receives invoices")
    ap.add_argument("--currency", default="USD")
    ap.add_argument("--return-url", default="https://example.com/return")
    ap.add_argument("--approve-wait", type=int, default=180)
    a = ap.parse_args()
    cfg = config.load()
    if cfg.mode != "sandbox" or not cfg.configured:
        print(f"Needs PAYPAL_MODE=sandbox and keys. {cfg.error or 'PAYPAL_MODE is ' + cfg.mode}")
        return 2
    pp = factory.build(cfg)
    c, cur = pp.client, a.currency

    step("OAuth2 client credentials", lambda: c.t._get_token() and True)

    # ---- Payouts
    d = _fresh()
    batch = step("Payouts: create", lambda: c.create_payout(d, amount_cents=100, currency=cur, receiver=a.receiver, note="Star sandbox check")["batch_header"]["payout_batch_id"],
                 skip="" if a.receiver else "no --receiver")
    EVIDENCE["batch_id"] = batch

    def duplicate():
        try:
            r = c.create_payout(d, amount_cents=100, currency=cur, receiver=a.receiver, note="Star sandbox check")
            print("      same key answered:", json.dumps({k: v for k, v in r.items() if k != "items"})[:400])
            assert r["batch_header"]["payout_batch_id"] == batch, "a second batch was created"
        except PayPalError as e:
            print("      duplicate rejection, exact shape:", json.dumps({"status": e.status, "name": e.name, "links": e.links, "details": e.details}))
    step("Payouts: identical re-POST finds the original, no second batch", duplicate, skip="" if batch else "no batch")

    def payout_status():
        for _ in range(10):
            b = c.get_payout_batch(batch)
            if (b.get("items") or [{}])[0].get("transaction_status") not in (None, "PENDING"):
                break
            time.sleep(3)
        print("      batch", b["batch_header"]["batch_status"], "item", b["items"][0].get("transaction_status"))
        return b["items"][0]["payout_item_id"]
    item = step("Payouts: status GET (batch and item)", payout_status, skip="" if batch else "no batch")
    step("Payouts: item GET", lambda: c.get_payout_item(item)["payout_item_id"], skip="" if item else "no item")

    def cancel():
        b = c.create_payout(_fresh(), amount_cents=100, currency=cur, receiver=a.unclaimed, note="Star sandbox check")["batch_header"]["payout_batch_id"]
        for _ in range(10):
            rows = c.get_payout_batch(b).get("items") or []
            if rows and rows[0].get("transaction_status") == "UNCLAIMED":
                break
            time.sleep(3)
        assert rows and rows[0]["transaction_status"] == "UNCLAIMED", "did not become UNCLAIMED"
        c.cancel_payout_item(rows[0]["payout_item_id"])
        status = c.get_payout_item(rows[0]["payout_item_id"])["transaction_status"]
        print("      after cancel:", status)
        assert status == "RETURNED", status
    step("Payouts: cancel an unclaimed item", cancel, skip="" if a.unclaimed else "no --unclaimed")

    # ---- Orders
    d2 = _fresh()
    order = step("Orders: create", lambda: c.create_order(d2, reference_id="check", items=[{"name": "Sandbox check", "quantity": 1, "unit_cents": 500}], currency=cur,
                                                          description="Star sandbox check", return_url=a.return_url, cancel_url=a.return_url))
    if order:
        print("      status:", order.get("status"), "links:", [l.get("rel") for l in order.get("links", [])])
    oid = (order or {}).get("id")
    EVIDENCE["order_id"] = oid
    step("Orders: same PayPal-Request-Id returns the same order", lambda: c.create_order(
        d2, reference_id="check", items=[{"name": "Sandbox check", "quantity": 1, "unit_cents": 500}], currency=cur, description="Star sandbox check",
        return_url=a.return_url, cancel_url=a.return_url)["id"] == oid or (_ for _ in ()).throw(AssertionError("a different order")), skip="" if oid else "no order")

    approved = False
    if oid and a.approve_wait:
        print(f"\nApprove here as the sandbox buyer, within {a.approve_wait}s:\n  {payer_link(order)}\n")
        end = time.time() + a.approve_wait
        while time.time() < end:
            if c.get_order(oid).get("status") == "APPROVED":
                approved = True
                break
            time.sleep(4)

    def capture():
        c.capture_order(oid, d2)
        o = c.get_order(oid)
        cap = o["purchase_units"][0]["payments"]["captures"][0]
        assert cap["status"] in ("COMPLETED", "PENDING"), cap["status"]
        assert cap.get("custom_id") == ids.custom_id(d2), "custom_id did not come back"
        return cap["id"]
    why = "" if approved else "order not approved"
    cap_id = step("Orders: capture, then verify by GET", capture, skip=why)
    EVIDENCE["capture_id"] = cap_id

    # ---- Refund
    refund = step("Payments: refund the capture", lambda: c.refund_capture(cap_id, _fresh())["id"], skip="" if cap_id else "no capture")
    step("Payments: refund GET", lambda: print("      status:", c.get_refund(refund).get("status")) or True, skip="" if refund else "no refund")

    # ---- Invoicing
    number = "STARCHK-" + secrets.token_hex(4)
    inv = step("Invoicing: create", lambda: (lambda r: r.get("id") or r["href"].rsplit("/", 1)[-1])(c.create_invoice(
        number=number, currency=cur, recipient_email=a.buyer, item_name="Sandbox check", amount_cents=500, note="Star sandbox check", reference="star-check")),
        skip="" if a.buyer else "no --buyer")
    EVIDENCE["invoice_id"] = inv
    step("Invoicing: send", lambda: c.send_invoice(inv) and True, skip="" if inv else "no invoice")
    step("Invoicing: search by invoice number", lambda: len(c.search_invoices({"invoice_number": number})["items"]) == 1 or (_ for _ in ()).throw(AssertionError("not found")),
         skip="" if inv else "no invoice")

    def invoice_get():
        r = c.get_invoice(inv)
        url = (r.get("detail", {}).get("metadata") or {}).get("recipient_view_url", "")
        print("      status:", r.get("status"), "recipient_view_url host:", url.split("/")[2] if url else "(none)")
        assert url, "no recipient_view_url"
    step("Invoicing: GET, with recipient_view_url", invoice_get, skip="" if inv else "no invoice")

    # ---- Reads
    step("Disputes: list", lambda: isinstance(c.list_disputes().get("items", []), list))
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400 * 7))
    step("Reporting: transactions", lambda: isinstance(c.list_transactions(start, now).get("transaction_details", []), list))
    hooks = step("Webhooks: list", lambda: c.list_webhooks().get("webhooks", []))
    if hooks is not None:
        print("      registered:", len(hooks))

    # ---- A known-bad signature must come back FAILURE
    def bad_signature():
        event = json.dumps({"id": "WH-STAR-CHECK", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {}}).encode()
        hdr = {"paypal-auth-algo": "SHA256withRSA", "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-INVALID",
               "paypal-transmission-id": "00000000-0000-0000-0000-000000000000", "paypal-transmission-sig": "Zm9yZ2Vk",
               "paypal-transmission-time": now}
        r = c.verify_webhook_signature(raw_event=event, headers=hdr, webhook_id=cfg.webhook_id)
        print("      answered:", r.get("verification_status"))
        assert r.get("verification_status") == "FAILURE"
    step("Webhooks: a forged signature is not verified", bad_signature, skip="" if cfg.webhook_id else "no PAYPAL_WEBHOOK_ID")

    out = Path(__file__).resolve().parent.parent / "data" / "sandbox-check.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"at": now, "results": RESULTS, "ids": EVIDENCE}, indent=1), encoding="utf-8")
    fails = sum(r["result"] == "FAIL" for r in RESULTS)
    print(f"\n{sum(r['result'] == 'PASS' for r in RESULTS)} passed, {fails} failed, {sum(r['result'] == 'SKIP' for r in RESULTS)} skipped. Wrote {out}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
