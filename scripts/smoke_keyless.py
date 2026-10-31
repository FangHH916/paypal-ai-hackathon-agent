"""Drive a running server through the whole keyless story over real HTTP, as a browser would, and check each step.

    python -m app.serve &     (or uvicorn app.server:app --port 8000)
    python scripts/smoke_keyless.py http://localhost:8000

Signs with a P-256 key like the browser's. The simulated buyer's click uses the simulator's own endpoint. Exit code is
nonzero if anything is wrong. Keyless: only ever talks to the server you point it at, in simulator mode.
"""
from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.kernel import p256  # noqa: E402

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8000"
FAILS: list[str] = []


def post(path, body=None, ok=True):
    r = requests.post(BASE + path, json=body or {}, timeout=30)
    if ok and r.status_code != 200:
        raise SystemExit(f"{path}: {r.status_code} {r.text[:400]}")
    return r.json()


def get(path, **params):
    return requests.get(BASE + path, params=params, timeout=30).json()


def check(name, cond, got=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{got}]" if got != "" else ""))
    if not cond:
        FAILS.append(name)


def b64(key, text):
    return base64.b64encode(p256.sign(key, text.encode())).decode()


class Customer:
    def __init__(self):
        self.sid = post("/api/reset", {"visitor": "feedfacefeedface", "world": True})["session_id"]
        self.key, jwk = p256.generate_keypair()
        post("/api/key", {"session_id": self.sid, "public_key": jwk})

    def say(self, text):
        return post("/api/chat", {"session_id": self.sid, "text": text})

    def confirm(self, card, wait=True):
        if card["cooldown_left"] and wait:
            time.sleep(card["cooldown_left"] + 1)
        body = {"session_id": self.sid, "draft_id": card["draft_id"], "digest": card["digest"]}
        if card["verify_question"]:
            body["verify_answer"] = True
        ch = post("/api/challenge", body)["payload"]
        return post("/api/confirm", {**body, "assertion": {"payload": ch, "signature": b64(self.key, ch)}})

    def payment(self, pid):
        return get(f"/api/payments/{self.sid}/{pid}")["payment"]

    def wait(self, pid, states, seconds=8):
        end = time.time() + seconds
        while time.time() < end:
            p = self.payment(pid)
            if p["state"] in states:
                return p
            time.sleep(0.4)
        return self.payment(pid)

    def writes(self):
        return get("/api/paypal/calls", session_id=self.sid)["money_moving_total"]


def main():
    health = get("/api/health")
    print("Server:", health["paypal"], health["engine"])
    check("simulator mode, labelled", health["paypal"]["mode"] == "simulator" and health["paypal"]["label"] == "SIMULATED")
    c = Customer()

    # ---- Send to family: a payout, verified only after settling
    r = c.say("Send 50 to my daughter")
    check("agent drafts, nothing sent", r["ui"]["type"] == "confirm" and c.writes() == 0, r["ui"]["type"])
    res = c.confirm(r["ui"]["card"])
    pid = res["payment"]["payment_id"]
    check("signed: a payout is in flight, not yet 'sent'", res["payment"]["state"] in ("PROCESSING", "SENT"), res["payment"]["state"])
    p = c.wait(pid, ("SENT",))
    check("SENT only from verified state", p["state"] == "SENT" and p["verified"], p["verified_by"])
    again = post("/api/confirm", {"session_id": c.sid, "draft_id": res["payment"]["draft_id"], "digest": r["ui"]["card"]["digest"], "assertion": {}})
    check("double click returns the same payment", again["payment"]["payment_id"] == pid and again["receipt"].get("idempotent_replay"))

    # ---- Agentic commerce: offers, stop, card, approval page, capture on return
    r = c.say("Get me a blood pressure monitor")
    card = r["ui"]["card"]
    check("three offers checked, one stopped", {o["verdict"] for o in card["offers_checked"]} == {"ok", "stop"} and len(card["offers_checked"]) == 3, r["reply"][:70])
    res = c.confirm(card)
    pay = res["payment"]
    check("awaiting approval, with a same-origin simulator link", pay["state"] == "AWAITING_APPROVAL" and res["next_step"]["url"].startswith("/simulator/checkout"))
    page = requests.get(BASE + res["next_step"]["url"], timeout=10)
    print("      simulator page status:", page.status_code, "(404 until the Product stage adds web/simulator.html)")
    token = res["next_step"]["url"].split("token=")[1]
    forged = post("/api/paypal/orders/return", {"session_id": c.sid, "payment_id": pay["payment_id"]})
    check("return without approval: not paid, no capture", forged["payment"]["state"] == "AWAITING_APPROVAL" and not forged["payment"]["verified"])
    info = get("/simulator/api/info", token=token)
    check("simulator info is labelled", info["simulated"] is True)
    dec = post("/simulator/api/decide", {"kind": "order", "id": token, "decision": "approve"})
    check("approval redirect goes back to the app", "pp_return=1" in dec["redirect"], dec["redirect"][:60])
    back = post("/api/paypal/orders/return", {"session_id": c.sid, "payment_id": pay["payment_id"]})
    check("PAID, verified by GET", back["payment"]["state"] == "PAID" and back["payment"]["verified"], back["payment"]["verified_by"][:40])
    check("capture id on record", bool(back["payment"]["paypal"].get("capture_id")))
    r2 = requests.get(f"{BASE}/?pp_return=1&payment={pay['payment_id']}", headers={"Host": BASE.split("//")[1]}, timeout=10, allow_redirects=False)
    check("GET on the return URL does nothing to the record", c.payment(pay["payment_id"])["updated_at"] == back["payment"]["updated_at"], r2.status_code)

    # ---- Voice refund
    r = c.say("Stop! I want my money back")
    check("refund drafted from the verified purchase", r["ui"].get("card", {}).get("kind") == "refund", r["reply"][:70])
    res = c.confirm(r["ui"]["card"])
    check("REFUNDED, verified", res["payment"]["state"] == "REFUNDED" and res["payment"]["verified"], res["payment"]["verified_by"][:40])

    # ---- The invoice that talked back
    before = c.writes()
    post("/api/demo/invoice-arrives", {"session_id": c.sid})
    r = c.say("What's this bill?")
    check("warned aloud", "looks wrong" in r["reply"], r["reply"][:90])
    check("the injected sentence never appears", "approve and pay" not in r["reply"].lower())
    r = c.say("Pay the SecureTech bill")
    check("code blocks it", r["ui"]["type"] == "blocked", r["reply"][:90])
    check("PayPal money-moving calls: 0 for this", c.writes() == before, c.writes())
    state = get("/api/state", session_id=c.sid)
    check("family alert raised", any("SecureTech" in a["text"] for a in state["alerts"]))

    # ---- The model mistake
    c2 = Customer()
    r = post("/api/demo/model-mistake", {"session_id": c2.sid})
    check("model mistake frozen", r["ui"]["type"] == "frozen" and c2.writes() == 0, r["reply"][:80])

    # ---- Guardian
    c3 = Customer()
    ch = post("/api/guardian/invite/challenge", {"session_id": c3.sid})["payload"]
    inv = post("/api/guardian/invite", {"session_id": c3.sid, "assertion": {"payload": ch, "signature": b64(c3.key, ch)}})
    gkey, gjwk = p256.generate_keypair()
    token = post("/api/guardian/pair", {"invite_token": inv["invite_token"], "public_key": gjwk})["token"]
    r = c3.say("Add a new person Tom Reed, email tom.reed@family.example")
    cid = r["ui"]["card"]["change_id"]
    ch = post("/api/change/challenge", {"session_id": c3.sid, "change_id": cid})["payload"]
    post("/api/change/confirm", {"session_id": c3.sid, "change_id": cid, "assertion": {"payload": ch, "signature": b64(c3.key, ch)}})
    r = c3.say("Pay Tom Reed 400 dollars")
    card = r["ui"]["card"]
    check("risky payment waits for the family", card["guardian_required"] and not card["can_confirm"])
    feed = post("/api/guardian/feed", {"token": token})
    check("Anna sees the exact card", feed["pending"][0]["digest"] == card["digest"], feed["pending"][0]["amount"])
    ch = post("/api/guardian/challenge", {"token": token, "draft_id": card["draft_id"], "decision": "approve"})["payload"]
    d = post("/api/guardian/decide", {"token": token, "payload": ch, "signature": b64(gkey, ch)})
    check("Anna's device signs the approval", d["decision"] == "approve")
    card = get(f"/api/card/{c3.sid}")["card"]
    check("now she can sign, after the wait", card["guardian_approved"] and not card["guardian_required"])
    res = c3.confirm(card)
    check("the payment goes through", res["payment"]["state"] in ("PROCESSING", "SENT"), res["payment"]["state"])

    # ---- Drill, comparison, lab, grid, chain
    drill = post("/api/lab/drill")
    check("failure drill: exactly one payout", drill["passed"] and drill["payouts_that_exist"] == 1, f"{drill['payout_requests_sent']} requests")
    naive = post("/api/lab/naive-agent")
    check("comparison: scripted agent paid, Star did not", naive["naive"]["paid"] and not naive["star"]["paid"] and naive["star"]["money_moving_calls"] == 0)
    lab = post("/api/lab")["results"]
    check(f"security lab: {sum(x['blocked'] for x in lab)}/{len(lab)} blocked", all(x["blocked"] for x in lab))
    rows = get("/api/activity", session_id=c.sid)
    check("activity grid has payments and stops", {"moved", "stopped"} <= {x["outcome"] for x in rows["rows"]}, len(rows["rows"]))
    check("audit chain intact", rows["chain"]["ok"], rows["chain"].get("records"))
    print(f"\n{'ALL PASSED' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
