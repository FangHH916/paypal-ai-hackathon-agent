"""HTTP API (Starlette). Run: uvicorn app.server:app --reload --port 8000

Money only moves through /api/confirm, which needs a signature made by the user's own device key over the exact
challenge issued for the card they saw (/api/key -> /api/challenge -> sign in the browser -> /api/confirm).
The Agent (model) has no way to call any of these. "Paid" and "Sent" are only ever read back from verified state: a
webhook whose signature PayPal itself verified, or an authenticated GET. A redirect or query string sets nothing, and
a capture never happens on a GET.
"""
from __future__ import annotations

import base64
import secrets
import threading
import time

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.middleware import Middleware
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from app import lab, runtime
from app.agent import presenter
from app.agent.llm import engine_label
from app.config import ROOT, settings
from app.kernel import contract
from app.kernel import protection as prot
from app.kernel.executor import KernelError
from app.kernel.hotline import hotline, hotline_words
from app.kernel.signing import Site
from app.kernel.state import UnknownSession, valid_visitor
from app.models import fmt_money
from app.paypal import calls as paypal_calls
from app.paypal.errors import PayPalConfigError, PayPalError, PayPalUnknown
from app.security import SecurityMiddleware, grid_csp
from app.voice import asr
from app.wallet import catalogue, payments
from app.wallet.guardian import GuardianError
from app.wallet.ledger import DEMO_USER

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _user_lock(user_id: str) -> threading.RLock:
    """One customer's payment calls run one at a time, in a worker thread, so a slow PayPal call never stalls the server
    and a double click can't race itself."""
    with _locks_guard:
        return _locks.setdefault(user_id, threading.RLock())


def _err(msg: str, code: str = "BAD_REQUEST", status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg, "code": code}, status_code=status)


class BadInput(Exception):
    pass


async def _json(request: Request, *required: str) -> dict:
    """Parse a JSON object body; missing fields or malformed JSON become a clean 400, never a 500."""
    try:
        body = await request.json()
    except ValueError:
        raise BadInput("Body must be valid JSON.")
    if not isinstance(body, dict):
        raise BadInput("Body must be a JSON object.")
    for key in ("session_id", "text", "reason", "engine", "token"):
        if key in body and (not isinstance(body[key], str) or (key == "session_id" and not body[key])):
            raise BadInput(f"{key} must be a non-empty string.")
    missing = [k for k in required if not isinstance(body.get(k), str) or not body.get(k)]
    if missing:
        raise BadInput(f"Missing field(s): {', '.join(missing)}")
    return body


async def _unknown_session(_: Request, exc: UnknownSession):
    return _err(str(exc), "SESSION_NOT_FOUND", 404)


async def _bad_input(_: Request, exc: BadInput):
    return _err(str(exc), "BAD_REQUEST", 400)


async def _guardian_error(_: Request, exc: GuardianError):
    return _err(str(exc), exc.code, 403 if exc.code in ("GUARDIAN_UNKNOWN", "GUARDIAN_SIGNATURE") else 400)


def _say(s, text: str) -> None:
    """Add a system message (receipt, cancel) to the history without creating two assistant turns in a row, which
    some model servers handle badly: merge it into the previous assistant message."""
    if s.messages and s.messages[-1]["role"] == "assistant":
        s.messages[-1] = {**s.messages[-1], "content": s.messages[-1]["content"] + "\n" + text}
    else:
        s.messages.append({"role": "assistant", "content": text})


def _site(request: Request) -> Site:
    """The passkey's website = the host the browser used (the middleware already restricts it to ALLOWED_HOSTS)."""
    host = request.headers.get("host", "localhost")
    rp_id = host.split("]")[0].lstrip("[") if host.startswith("[") else host.rsplit(":", 1)[0]
    return Site(rp_id.lower(), frozenset({f"http://{host}", f"https://{host}"}))


def _return_base(request: Request) -> str:
    """Where PayPal sends the buyer back to. Not signed and not trusted: the return handler verifies everything itself."""
    return settings.public_base_url.rstrip("/") or f"{request.url.scheme}://{request.headers.get('host', 'localhost')}"


def _page(name: str) -> Response:
    """A page that may show the grid: it gets its own CSP with a fresh style nonce. The script policy doesn't change."""
    nonce = secrets.token_urlsafe(16)
    html = (ROOT / "web" / name).read_bytes().replace(b"__STYLE_NONCE__", nonce.encode())
    return Response(html, media_type="text/html; charset=utf-8", headers={"content-security-policy": grid_csp(nonce)})


async def index(request: Request):
    # Passkeys (fingerprint / face) only work on a domain name, never an IP address. uvicorn prints
    # http://127.0.0.1:8000, so send loopback-IP visitors to the same server under "localhost".
    host = request.headers.get("host", "")
    name, _, port = host.rpartition(":") if not host.endswith("]") else (host, "", "")
    if (name or host) in ("127.0.0.1", "[::1]") and "localhost" in settings.allowed_hosts:
        query = "?" + request.url.query if request.url.query else ""     # the PayPal return link's query must survive this
        return RedirectResponse(f"http://localhost{':' + port if port else ''}/{query}", status_code=307)
    return _page("index.html")


async def guardian_page(_: Request):
    return _page("guardian.html") if (ROOT / "web" / "guardian.html").exists() else _err("Not found.", "NOT_FOUND", 404)


# Only these files are ever served: source code and .env can never be fetched.
STATIC = {"/styles.css": ("styles.css", "text/css"), "/app.js": ("app.js", "text/javascript"),
          "/logo.svg": ("logo.svg", "image/svg+xml"), "/favicon.svg": ("favicon.svg", "image/svg+xml"),
          "/activity.js": ("activity.js", "text/javascript"), "/guardian.js": ("guardian.js", "text/javascript"),
          "/simulator.js": ("simulator.js", "text/javascript"),
          "/vendor/ag-grid/ag-grid-community.min.js": ("vendor/ag-grid/ag-grid-community.min.js", "text/javascript")}
_CACHED = {"/vendor/ag-grid/ag-grid-community.min.js"}     # 2 MB: otherwise no-store downloads it on every visit


async def static(request: Request):
    name, mime = STATIC[request.url.path]
    path = ROOT / "web" / name
    if not path.exists():
        return _err("Not found.", "NOT_FOUND", 404)
    headers = {"cache-control": "public, max-age=86400"} if request.url.path in _CACHED else None
    return Response(path.read_bytes(), media_type=mime + ("; charset=utf-8" if "svg" not in mime else ""), headers=headers)


def _pp_status() -> dict:
    return runtime.execution_gateway.mode_status()


def _invoice_view(i: dict) -> dict:
    return {"invoice_id": i["invoice_id"], "number": i["number"], "title": i["title"], "from": i["invoicer_name"], "amount": fmt_money(i["amount_cents"]),
            "amount_cents": i["amount_cents"], "due": i["due"], "status": i["status"], "known_biller": not i["payee_id"].startswith("invoicer:")}


def _payment_view(rec: dict) -> dict:
    """One record for the browser: the tracker, the receipt, the tag. handle masked; no secrets exist here anyway."""
    gw = runtime.execution_gateway
    items = rec["card"].get("items") or []
    return {"payment_id": rec["payment_id"], "draft_id": rec["draft_id"], "kind": rec["kind"], "rail": rec["rail"], "state": rec["state"],
            "status": payments.status_of(rec), "verified": rec["verified"], "verified_by": rec["verified_by"], "verified_at": rec["verified_at"],
            "amount": fmt_money(rec["amount_cents"]), "amount_cents": rec["amount_cents"], "currency": rec["currency"],
            "to": rec["counterparty_name"], "handle": payments.mask_email(rec["counterparty_handle"]),
            "what": items[0]["name"] if items else rec["counterparty_name"], "created_at": rec["created_at"], "updated_at": rec["updated_at"],
            "refunded_cents": rec.get("refunded_cents", 0), "parent_payment_id": rec.get("parent_payment_id"),
            "paypal": {k: v for k, v in rec["paypal"].items() if v and k not in ("debug_ids", "stage", "return_base", "approve_url")},
            "evidence": rec["evidence"], "event_ids": rec["event_ids"], "next_step": gw.next_step(rec), "mode_label": gw.mode_status()["label"],
            "digest": rec["digest"], "idempotency_key": rec["idempotency_key"], "receipt": payments.receipt_body(rec, gw.mode_status()["mode"])}


async def state(request: Request):
    """Everything the dashboard shows: allowance, people, invoices, history, this session's records and status."""
    s = runtime.sessions.get(request.query_params.get("session_id"))
    wallet, uid = runtime.wallet, s.user_id
    user = wallet.user(uid)
    await run_in_threadpool(runtime.execution_gateway.invoices.sync, uid)
    lim = wallet.limits(uid)
    return JSONResponse({
        "session_id": s.session_id, "phase": s.phase.value, "user": user["name"], "full_name": user["full_name"],
        "currency": settings.wallet_currency, "paypal": _pp_status(), "engine": engine_label(runtime.llm),
        "allowance": {"left": fmt_money(wallet.allowance_left(uid)), "left_cents": wallet.allowance_left(uid),
                      "monthly": fmt_money(user["allowance"]["monthly_cents"]),
                      "note": "Star can't see your PayPal balance. This is the limit you and your family set."},
        "accounts": [{**a, "balance": fmt_money(a["balance_cents"])} for a in wallet.accounts(uid)],
        "total_balance": fmt_money(sum(a["balance_cents"] for a in wallet.accounts(uid))),
        "payees": [{k: p[k] for k in ("payee_id", "name", "relation", "type", "masked_account")} for p in wallet.payees(uid)],
        "invoices": [_invoice_view(i) for i in wallet.invoices(uid, only_open=False)],
        "transactions": [{**t, "amount": ("+" if t["amount_cents"] > 0 else "−") + fmt_money(abs(t["amount_cents"]))}
                         for t in wallet.transactions(uid, limit=30)],
        "receipts": [payments.receipt_body(r, _pp_status()["mode"]) for r in wallet.payments_for(uid)],
        "device_key": bool(s.public_key),
        "passkey": runtime.kernel.passkey_status(s.user_id, _site(request)),
        "protection": prot.status(wallet, uid),
        "limits": {"daily": fmt_money(lim["daily_transfer_cents"]), "used_today": fmt_money(wallet.transferred_today(uid)),
                   "pending": fmt_money(lim["pending"]["to_cents"]) if lim.get("pending") else None},
        "guardian": runtime.execution_gateway.guardian.status(uid),
        "alerts": user["alerts"][:20], "frozen": {k: user["frozen"][k] for k in ("on", "by", "at", "lift")},
        "hotline": hotline(), "pending_change": (s.pending_change or {}).get("card"), "scam_level": s.scam_level.value,
    })


async def audit_export(request: Request):
    s = runtime.sessions.get(request.path_params["session_id"])
    return JSONResponse({"session_id": s.session_id, "integrity": runtime.audit.verify(),
                         "entries": runtime.audit.for_session(s.session_id)})


async def health(_: Request):
    return JSONResponse({"llm": getattr(runtime.llm, "name", "?"), "engine": engine_label(runtime.llm),
                         "model": settings.llm_model if settings.llm_enabled else "offline-rules",
                         "asr": settings.asr_provider if settings.asr_enabled else "browser-fallback",
                         "verifier": runtime.verifier.name, "public_demo": settings.public_demo,
                         "browser_key": settings.allow_browser_key, "paypal": _pp_status()})


async def chat(request: Request):
    body = await _json(request)
    text = (body.get("text") or "").strip()
    if not text:
        return _err("text must not be empty")
    s = runtime.sessions.get(body.get("session_id"))
    if len(text) > 500:
        return _err("Please keep each message within 500 characters; nothing was prepared.")
    if isinstance(body.get("care"), bool):
        # the screen the customer is looking at decides how Star talks (survives a server restart)
        s.care_mode = body["care"]
    from app.kernel.voice import bind_utterance
    s.turn_audio = bind_utterance(s, text, body.get("audio_sha256"))   # spoken: link the words to the recording

    def turn():
        with _user_lock(s.user_id), paypal_calls.scope(s.user_id, s.session_id):
            return runtime.agent.run_turn(s, text)
    try:
        result = await run_in_threadpool(turn)
    finally:
        s.turn_audio = None
    return JSONResponse({"session_id": s.session_id, "phase": s.phase.value, **result.to_dict()})


async def speech_to_text(request: Request):
    body = await _json(request)
    if not settings.asr_enabled:
        return _err("Server speech recognition is not configured; the frontend will use browser speech recognition.", "ASR_DISABLED", 501)
    try:
        wav = base64.b64decode(body["audio_b64"])
    except Exception:  # noqa: BLE001
        return _err("audio_b64 is not valid base64")
    s = runtime.sessions.get(body.get("session_id"))
    prompt = asr.build_prompt(runtime.wallet.payees(s.user_id))
    try:
        res = await run_in_threadpool(asr.recognize, wav, prompt)
    except asr.ASRError as e:
        return _err(str(e), "ASR_ERROR", 502)
    s.add_trace("asr", f"Speech recognition ({res['engine']}, {res['latency_ms']}ms): {res['text']}")
    from app.kernel.voice import record_clip
    try:
        res["audio_sha256"] = record_clip(s, runtime.voices, runtime.audit, wav, res)   # kept for the audit trail
    except OSError as e:
        s.add_trace("asr", f"Recording could not be stored: {e}")
    return JSONResponse(res)


async def voice_clip(request: Request):
    """Play back one of this session's own recordings (Audit trail page)."""
    s = runtime.sessions.get(request.path_params["session_id"])
    sha = request.path_params["sha"]
    path = runtime.voices.path(sha) if sha in s.voice_clips else None
    if not path:
        return _err("Recording not found (recordings are kept until the demo server restarts).", "NOT_FOUND", 404)
    return FileResponse(path, media_type="audio/wav", filename=f"voice-{sha[:12]}.wav")


async def register_key(request: Request):
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    try:
        key_id = runtime.kernel.register_key(s, body.get("public_key"))
    except KernelError as e:
        return _err(str(e), e.code)
    return JSONResponse({"key_id": key_id})


async def challenge(request: Request):
    body = await _json(request, "draft_id", "digest")
    s = runtime.sessions.get(body.get("session_id"))
    try:
        payload = runtime.kernel.issue_challenge(s, body["draft_id"], body["digest"],
                                                 verify_answer=body.get("verify_answer"))
    except KernelError as e:
        d = s.drafts.get(body["draft_id"])
        if d:
            runtime.kernel.record_decision(s, d, stage="authorization_rejected", rejection=e.code)
        return JSONResponse({"error": str(e), "code": e.code, "decision": d.decision if d else None}, status_code=400)
    return JSONResponse({"payload": payload, "passkey": runtime.kernel.passkey_status(s.user_id, _site(request))})


async def passkey_options(request: Request):
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    try:
        return JSONResponse(runtime.kernel.passkey_options(s, _site(request)))
    except KernelError as e:
        return _err(str(e), e.code)


async def passkey_register(request: Request):
    body = await _json(request, "credential_id", "client_data_json", "attestation_object")
    s = runtime.sessions.get(body.get("session_id"))
    try:
        key_id = runtime.kernel.passkey_register(s, _site(request), body["credential_id"], body["client_data_json"],
                                                 body["attestation_object"])
    except KernelError as e:
        return _err(str(e), e.code)
    return JSONResponse({"key_id": key_id, **runtime.kernel.passkey_status(s.user_id, _site(request))})


async def passkey_remove(request: Request):
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    try:
        return JSONResponse({"removed": runtime.kernel.passkey_remove(s, _site(request), body.get("assertion"))})
    except KernelError as e:
        return _err(str(e), e.code)


async def passkey_remove_challenge(request: Request):
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    return JSONResponse({"payload": runtime.kernel.passkey_remove_challenge(s, _site(request)),
                         "passkey": runtime.kernel.passkey_status(s.user_id, _site(request))})


async def transaction_status(request: Request):
    s = runtime.sessions.get(request.path_params["session_id"])
    rec = runtime.wallet.payment_by_draft(s.user_id, request.path_params["draft_id"])
    if not rec:
        return JSONResponse({"status": "NOT_POSTED", "phase": s.phase.value})
    await run_in_threadpool(_refresh_one, rec)
    d = s.drafts.get(rec["draft_id"])
    text = presenter.receipt_text(d, runtime.execution_gateway.receipt(rec)) if d else f"{rec['counterparty_name']}: {rec['state']}"
    return JSONResponse({"status": payments.status_of(rec), "payment": _payment_view(rec), "reply": text, "speak": text, "trace": []})


async def protection_challenge(request: Request):
    body = await _json(request, "to")
    s = runtime.sessions.get(body.get("session_id"))
    try:
        payload = runtime.kernel.protection_challenge(s, body["to"])
    except KernelError as e:
        return _err(str(e), e.code)
    return JSONResponse({"payload": payload, "passkey": runtime.kernel.passkey_status(s.user_id, _site(request))})


async def protection_change(request: Request):
    body = await _json(request, "to")
    s = runtime.sessions.get(body.get("session_id"))
    try:
        st = runtime.kernel.change_protection(s, body["to"], body.get("assertion"), _site(request))
    except KernelError as e:
        return _err(str(e), e.code)
    return JSONResponse(st)


# ---- Account changes (add a person, raise the limit): signed on the device like a payment
async def change_challenge(request: Request):
    body = await _json(request, "change_id")
    s = runtime.sessions.get(body.get("session_id"))
    try:
        payload = runtime.kernel.change_challenge(s, body["change_id"])
    except KernelError as e:
        return _err(str(e), e.code)
    return JSONResponse({"payload": payload, "passkey": runtime.kernel.passkey_status(s.user_id, _site(request))})


async def change_confirm(request: Request):
    body = await _json(request, "change_id")
    s = runtime.sessions.get(body.get("session_id"))
    start = len(s.trace)
    try:
        out = runtime.kernel.apply_change(s, body["change_id"], body.get("assertion"), _site(request))
    except KernelError as e:
        return JSONResponse({"error": str(e), "code": e.code, "trace": s.trace[start:]}, status_code=400)
    _say(s, out["reply"])
    next_task = s.plan.pop(0) if s.plan else None
    return JSONResponse({"reply": out["reply"], "speak": out["reply"], "method": out["method"], "next_task": next_task,
                         "trace": s.trace[start:]})


async def change_cancel(request: Request):
    body = await _json(request, "change_id")
    s = runtime.sessions.get(body.get("session_id"))
    try:
        runtime.kernel.cancel_change(s, body["change_id"])
    except KernelError as e:
        return _err(str(e), e.code)
    msg = "OK, cancelled. Nothing was changed."
    _say(s, msg)
    return JSONResponse({"reply": msg, "speak": msg})


async def demo_model_mistake(request: Request):
    """Live demo: one turn with a model that writes the wrong amount, through the REAL verifier, kernel and gateway."""
    from app.agent.loop import Agent
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    text = (body.get("text") or "Send 50 to my daughter").strip()[:200]
    result = Agent(runtime.wallet, runtime.gateway, runtime.audit, lab.MistakenLLM()).run_turn(s, text)
    # Tell the app this was the fault-injection demo, so it can show the comparison instead of a puzzling refusal
    d = s.current_draft
    heard = ((d.verification or {}).get("extracted") or {}) if d else {}
    return JSONResponse({"session_id": s.session_id, "phase": s.phase.value, **result.to_dict(),
                         "demo": {"kind": "model_mistake", "fault": "The demo model multiplies every amount by 10",
                                  "said": text, "heard_amount": heard.get("amount"), "heard_payee": heard.get("payee")}})


async def demo_invoice_arrives(request: Request):
    """The demo's pacing control: the fake invoice arrives now. Simulator only (in the sandbox the seed script sends it)."""
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    row = await run_in_threadpool(runtime.execution_gateway.invoices.arrive_fake, s.user_id)
    if not row:
        return _err("Only the simulator has this control. In the sandbox run scripts/paypal_seed_sandbox.py.", "SIMULATOR_ONLY", 404)
    return JSONResponse({"invoice": _invoice_view(row), "simulated": True})


async def run_security_lab(_: Request):
    return JSONResponse({"results": await run_in_threadpool(lab.run_lab)})


async def contract_lab(_: Request):
    from app.contract_lab import run_showcase
    return JSONResponse(await run_in_threadpool(run_showcase))


async def lab_drill(_: Request):
    if not runtime.execution_gateway.simulator:
        return _err("The failure drill only runs in simulator mode.", "SIMULATOR_ONLY", 404)
    return JSONResponse(await run_in_threadpool(lab.run_drill))


async def lab_naive_agent(_: Request):
    if not runtime.execution_gateway.simulator:
        return _err("The comparison only runs in simulator mode.", "SIMULATOR_ONLY", 404)
    return JSONResponse(await run_in_threadpool(lab.run_naive_agent))


async def confirm(request: Request):
    body = await _json(request, "draft_id", "digest")
    s = runtime.sessions.get(body.get("session_id"))
    site, base = _site(request), _return_base(request)

    def work():
        start = len(s.trace)
        with _user_lock(s.user_id), paypal_calls.scope(s.user_id, s.session_id):
            existing = runtime.wallet.payment_by_draft(s.user_id, body["draft_id"])
            if existing:               # a double click: the same record, never a second payment
                receipt = runtime.execution_gateway.receipt(existing, replay=True)
                d = s.drafts.get(body["draft_id"])
                text = presenter.receipt_text(d, receipt) if d else ""
                return JSONResponse({"receipt": receipt, "payment": _payment_view(existing), "next_step": receipt["next_step"], "reply": text,
                                     "speak": text, "next_task": None, "trace": [], "phase": s.phase.value})
            try:
                token = runtime.kernel.authorize(s, body["draft_id"], body["digest"], body.get("assertion", ""),
                                                 verify_answer=body.get("verify_answer"), site=site)
                receipt = runtime.kernel.commit(s, body["draft_id"], token, return_base=base)
            except KernelError as e:
                d = s.drafts.get(body["draft_id"])
                if d:
                    runtime.kernel.record_decision(s, d, stage="rejected", rejection=e.code)
                return JSONResponse({"error": str(e), "code": e.code, "trace": s.trace[start:],
                                     "decision": d.decision if d else None}, status_code=400)
            draft = s.drafts[body["draft_id"]]
            text = presenter.receipt_text(draft, receipt)
            _say(s, text)
            s.settle()
            next_task = s.plan.pop(0) if s.plan else None
            return JSONResponse({"receipt": receipt, "payment": _payment_view(runtime.wallet.payment(receipt["payment_id"])),
                                 "next_step": receipt["next_step"], "reply": text, "speak": text, "next_task": next_task,
                                 "trace": s.trace[start:], "phase": s.phase.value})
    return await run_in_threadpool(work)


async def cancel(request: Request):
    body = await _json(request, "draft_id")
    s = runtime.sessions.get(body.get("session_id"))
    try:
        runtime.kernel.cancel(s, body["draft_id"], reason=body.get("reason", "user"))
    except KernelError as e:
        return _err(str(e), e.code)
    s.settle()
    msg = "OK, it's cancelled. No money was sent."
    if body.get("reason") == "new_payee_not_own_idea":
        d = s.drafts[body["draft_id"]]
        msg = (f"I've stopped this payment to {d.payee_name}. If someone on the phone or in a message asked you to pay a new "
               f"person, it is very likely a scam. Please don't send it. Call {hotline_words()}, or talk to your "
               "family first.")
        runtime.wallet.add_stop(s.user_id, kind=d.kind, to=d.payee_name, amount_cents=d.amount_cents, status="STOPPED",
                                stopped_by="She said it wasn't her own idea", why=msg, codes=["NEW_PAYEE_NOT_OWN_IDEA"],
                                draft_id=d.draft_id, digest=d.digest, session_id=s.session_id)
        runtime.wallet.add_alert(s.user_id, "stop", "stopped", f"Mrs. Lee stopped a payment to {d.payee_name}: it wasn't her own idea.", d.draft_id)
    _say(s, msg)
    next_task = s.plan.pop(0) if s.plan else None      # offered as a button, never run automatically
    return JSONResponse({"reply": msg, "speak": msg, "phase": s.phase.value, "next_task": next_task,
                         "decision": s.drafts[body["draft_id"]].decision})


async def current_card(request: Request):
    s = runtime.sessions.get(request.path_params["session_id"])
    d = s.current_draft
    if not d:
        return JSONResponse({"card": None, "phase": s.phase.value})
    # once the cooling-off period ends, move to awaiting authorization
    runtime.kernel._refresh_cooling(s, d)  # noqa: SLF001
    if s.phase.value in ("COOLING", "AWAITING_AUTH"):
        d.decision = contract.decision(d, s, runtime.wallet, runtime.kernel.approvals)
    card = presenter.build_card(d, runtime.wallet, s.phase, care=s.care_mode, offers=s.last_offers, mode_label=_pp_status()["label"])
    return JSONResponse({"card": card, "phase": s.phase.value})


async def trace(request: Request):
    s = runtime.sessions.get(request.path_params["session_id"])
    return JSONResponse({"trace": s.trace, "audit": runtime.audit.for_session(s.session_id)[-50:]})


async def audit_verify(_: Request):
    return JSONResponse(runtime.audit.verify())


async def care(request: Request):
    """The Simple view switch in the app: keep Star's way of talking in step with the screen."""
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    s.care_mode, s.care_offer_pending = bool(body.get("on")), False
    runtime.audit.append("ui.care_mode", s.session_id, {"on": s.care_mode, "by": "switch"})
    return JSONResponse({"care_mode": s.care_mode})


async def care_offer(request: Request):
    """When the customer arrives, Star asks once whether they'd like the easier version."""
    from app.agent.loop import CARE_OFFER
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    if s.care_mode or any(m.get("role") == "user" for m in s.messages):
        return JSONResponse({"offer": None})
    text = CARE_OFFER.format(name=runtime.wallet.user(s.user_id)["name"])
    s.messages.append({"role": "assistant", "content": text})
    s.care_offer_pending = True
    return JSONResponse({"offer": text, "options": [
        {"label": "Yes, make it easier", "detail": "Bigger text, simpler words, read aloud", "send": "Yes, please"},
        {"label": "No, standard is fine", "detail": "You can switch any time", "send": "No, thanks"}]})


async def reset(request: Request):
    body = await _json(request)
    sid = body.get("session_id")
    try:
        sid and runtime.sessions.get(sid)
    except (UnknownSession, ValueError):
        sid = None                      # the server restarted and forgot it: reset just starts a new session
    visitor = valid_visitor(body.get("visitor"))
    if visitor:
        # Online demo: this browser's own demo customer. "Reset demo" resets only their world, never anyone else's;
        # their passkey stays enrolled.
        s = runtime.sessions.reset(sid) if sid else runtime.sessions.new_for_visitor(visitor)
        if body.get("world"):
            runtime.wallet.reset_user(s.user_id)
            runtime.paypal.log.clear_user(s.user_id)
    else:
        if body.get("world"):
            if settings.public_demo:
                runtime.wallet.reset_user(DEMO_USER)     # a shared server never wipes every visitor at once
            else:
                runtime.wallet.reset()
            runtime.paypal.log.clear_user(DEMO_USER)
        s = runtime.sessions.reset(sid) if sid else runtime.sessions.get(None)
    return JSONResponse({"session_id": s.session_id, "ts": time.time()})


# ---- PayPal: verified state, and the buyer's way back
def _refresh_all(user_id: str) -> None:
    with _user_lock(user_id):
        runtime.execution_gateway.reconciler.refresh_user(user_id)


def _refresh_one(rec: dict) -> None:
    with _user_lock(rec["user_id"]):
        runtime.execution_gateway.reconciler.pump()
        runtime.execution_gateway.reconciler.refresh(rec)


def _own_payment(s, payment_id: str) -> dict:
    rec = runtime.wallet.payment(payment_id or "")
    if not rec or rec["user_id"] != s.user_id:
        raise BadInput("That payment isn't yours.")
    return rec


async def list_payments(request: Request):
    s = runtime.sessions.get(request.query_params.get("session_id"))
    await run_in_threadpool(_refresh_all, s.user_id)
    return JSONResponse({"payments": [_payment_view(r) for r in runtime.wallet.payments_for(s.user_id)], "mode_label": _pp_status()["label"]})


async def one_payment(request: Request):
    s = runtime.sessions.get(request.path_params["session_id"])
    rec = _own_payment(s, request.path_params["payment_id"])
    await run_in_threadpool(_refresh_one, rec)
    return JSONResponse({"payment": _payment_view(rec)})


async def orders_return(request: Request):
    """She came back from approving. This is a POST from the app, never the redirect itself: capture never happens on a
    GET. Whatever the query string claimed, the state here comes from PayPal."""
    body = await _json(request, "payment_id")
    s = runtime.sessions.get(body.get("session_id"))
    rec = _own_payment(s, body["payment_id"])

    def work():
        with _user_lock(s.user_id):
            return runtime.execution_gateway.reconciler.order_return(rec)
    rec = await run_in_threadpool(work)
    d = s.drafts.get(rec["draft_id"])
    receipt = runtime.execution_gateway.receipt(rec)
    text = "PayPal confirms it's paid." if rec["state"] == "PAID" and rec["verified"] else (
        presenter.receipt_text(d, receipt) if d else "Checking with PayPal…")
    return JSONResponse({"payment": _payment_view(rec), "reply": text, "speak": text})


async def orders_cancelled(request: Request):
    body = await _json(request, "payment_id")
    s = runtime.sessions.get(body.get("session_id"))
    rec = _own_payment(s, body["payment_id"])

    def work():
        with _user_lock(s.user_id):
            return runtime.execution_gateway.reconciler.order_cancelled(rec)
    rec = await run_in_threadpool(work)
    text = "You cancelled it on the approval page. No money moved." if rec["state"] == "BUYER_CANCELLED" else "That payment had already gone through."
    return JSONResponse({"payment": _payment_view(rec), "reply": text, "speak": text})


async def paypal_webhook(request: Request):
    """Public, no session. Only PayPal's own verification of the signature makes this do anything (app/paypal/webhooks.py)."""
    raw = await request.body()
    res = await run_in_threadpool(runtime.execution_gateway.webhooks.handle, raw, dict(request.headers))
    return JSONResponse({"code": res.code, **({"error": res.detail} if res.status >= 400 else {})}, status_code=res.status)


async def paypal_calls_log(request: Request):
    """The ticker. It comes from the server log, never computed in the browser. No secrets exist in these records."""
    s = runtime.sessions.get(request.query_params.get("session_id"))
    log = runtime.paypal.log
    return JSONResponse({"calls": log.for_user(s.user_id)[-40:], "mode_label": _pp_status()["label"],
                         "money_moving_this_task": log.money_moving_count(s.user_id, s.task_started_at),
                         "money_moving_total": log.money_moving_count(s.user_id)})


async def catalogue_view(request: Request):
    from app.kernel.sanitize import has_instruction, sanitize
    from app.kernel.trust import offer_verdict
    s = runtime.sessions.get(request.query_params.get("session_id"))
    rows = []
    for o in catalogue.OFFERS:
        m = catalogue.merchant(o["merchant_id"])
        rows.append({"offer_id": o["offer_id"], "name": o["name"], "merchant": m["name"], "price": fmt_money(o["price_cents"]),
                     "usual_price": fmt_money(o["reference_cents"]), "category": o["category"], "listing": sanitize(m["description"]),
                     "suspicious_removed": has_instruction(m["description"]), **offer_verdict(o, runtime.wallet, s.user_id, s.scam_level.value)})
    return JSONResponse({"offers": rows})


async def invoices_view(request: Request):
    s = runtime.sessions.get(request.query_params.get("session_id"))
    rows = await run_in_threadpool(runtime.execution_gateway.invoices.sync, s.user_id)
    return JSONResponse({"invoices": [_invoice_view(i) for i in rows], "mode_label": _pp_status()["label"]})


async def disputes_view(request: Request):
    runtime.sessions.get(request.query_params.get("session_id"))
    try:
        res = await run_in_threadpool(runtime.execution_gateway.reader.list_disputes)
    except (PayPalUnknown, PayPalError, PayPalConfigError):
        return JSONResponse({"available": False, "disputes": [], "mode_label": _pp_status()["label"]})
    return JSONResponse({"available": True, "disputes": res.get("items") or [], "mode_label": _pp_status()["label"],
                         "note": "Read only. In this demo these are cases on the household's own account."})


def activity_rows(user_id: str) -> list[dict]:
    """Rows of the Activity & audit grid: every payment and every stop, with a link into the audit chain. Emails masked."""
    hashes: dict[str, str] = {}
    records = runtime.wallet.payments_for(user_id)
    stops = runtime.wallet.stops(user_id)
    for sid in {r["session_id"] for r in records} | {x["session_id"] for x in stops if x["session_id"]}:
        for entry in runtime.audit.for_session(sid):
            data = entry.get("data") or {}
            key = data.get("draft_id") or data.get("transaction_id") or data.get("draft", {}).get("draft_id") if isinstance(data, dict) else None
            if key:
                hashes[key] = entry["hash"]
    rows = [payments.activity_row(r, hashes.get(r["draft_id"], "")) for r in records]
    for x in stops:
        rows.append({"row_id": x["row_id"], "time": x["time"], "updated": x["time"], "type": "Stopped" if x["status"] != "DENIED" else "Family said no",
                     "kind": x["kind"], "to": x["to"], "handle": "", "amount_cents": -x["amount_cents"] if x["kind"] in payments.SPENDS else x["amount_cents"],
                     "currency": settings.wallet_currency, "status": x["status"], "verified": False, "verified_by": "", "stopped_by": x["stopped_by"], "why": x["why"],
                     "reference": "", "signed_with": "", "family": "Denied" if x["status"] == "DENIED" else "", "audit_hash": hashes.get(x["draft_id"], "")[:16],
                     "outcome": "stopped", "digest": x["digest"], "codes": x["codes"]})
    rows.sort(key=lambda r: r["time"], reverse=True)
    return rows


async def activity(request: Request):
    s = runtime.sessions.get(request.query_params.get("session_id"))
    await run_in_threadpool(_refresh_all, s.user_id)
    return JSONResponse({"rows": activity_rows(s.user_id), "mode_label": _pp_status()["label"], "chain": runtime.audit.verify()})


# ---- Freeze: instant on, two people to lift
async def freeze(request: Request):
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    return JSONResponse({"frozen": runtime.kernel.freeze(s, "the customer")})


async def freeze_lift_challenge(request: Request):
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    return JSONResponse({"payload": runtime.kernel.lift_freeze_challenge(s), "passkey": runtime.kernel.passkey_status(s.user_id, _site(request))})


async def freeze_lift(request: Request):
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    try:
        return JSONResponse({"frozen": runtime.kernel.lift_freeze(s, body.get("assertion"), _site(request))})
    except KernelError as e:
        return _err(str(e), e.code)


# ---- The family guardian: her own device, her own key
def _desk():
    return runtime.execution_gateway.guardian


def _guardian_user(body: dict) -> str:
    return _desk().user_for_token(body.get("token") or "")


async def guardian_invite_challenge(request: Request):
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    from app.kernel.signing import build_settings_challenge
    payload = build_settings_challenge(s.session_id, s.user_id, {"guardian_invite": True})
    s.setting_challenges["guardian_invite"] = payload
    return JSONResponse({"payload": payload, "passkey": runtime.kernel.passkey_status(s.user_id, _site(request))})


async def guardian_invite(request: Request):
    """Mrs. Lee signs the invite, so nobody can pair themselves into her account by talking to the model."""
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    issued, assertion = s.setting_challenges.get("guardian_invite"), body.get("assertion")
    if not issued or not isinstance(assertion, dict) or assertion.get("payload") != issued:
        return _err("Please confirm this with your fingerprint.", "PAYLOAD_MISMATCH")
    try:
        runtime.kernel._signature(s, issued, assertion, _site(request))  # noqa: SLF001
    except KernelError as e:
        return _err(str(e), e.code)
    s.setting_challenges.pop("guardian_invite", None)
    token = _desk().invite(s.user_id, s.session_id)
    return JSONResponse({"invite_token": token, "link": f"/guardian?invite={token}", "expires_in": 600})


async def guardian_pair(request: Request):
    body = await _json(request, "invite_token")
    return JSONResponse(_desk().pair(body["invite_token"], body.get("public_key") or {}))


async def guardian_feed(request: Request):
    body = await _json(request)
    uid = _guardian_user(body)
    await run_in_threadpool(_refresh_all, uid)
    feed = _desk().feed(uid)
    feed["activity"] = activity_rows(uid)
    feed["mode_label"] = _pp_status()["label"]
    return JSONResponse(feed)


async def guardian_challenge(request: Request):
    body = await _json(request, "draft_id", "decision")
    uid = _guardian_user(body)
    return JSONResponse({"payload": _desk().challenge(uid, body["draft_id"], body["decision"])})


async def guardian_decide(request: Request):
    body = await _json(request, "payload", "signature")
    uid = _guardian_user(body)
    row = _desk().decide(uid, body["payload"], body["signature"])
    s = runtime.sessions.get(row["session_id"])
    signed = row["signed"]
    try:
        if row["decision"] == "approve":
            runtime.kernel.approve_guardian(s, row["draft_id"], row["digest"], signed)
            return JSONResponse({"decision": "approve", "reply": "Approved. She can now sign this exact card. Nothing has moved yet."})
        runtime.kernel.deny_guardian(s, row["draft_id"], row["digest"], signed)
        _say(s, "Your family said no to this payment, so nothing was sent.")
        s.settle()
        return JSONResponse({"decision": "deny", "reply": "Denied. The payment is dead and nothing was sent."})
    except KernelError as e:
        return _err(str(e), e.code)


async def guardian_freeze(request: Request):
    body = await _json(request)
    uid = _guardian_user(body)
    f = runtime.wallet.set_frozen(uid, True, "the family guardian")
    runtime.audit.append("wallet.frozen", "", {"by": "the family guardian"})
    runtime.wallet.add_alert(uid, "stop", "freeze", "Payments were frozen by the family guardian.")
    return JSONResponse({"frozen": f})


async def guardian_lift_challenge(request: Request):
    body = await _json(request)
    _guardian_user(body)
    return JSONResponse({"payload": _desk().lift_challenge()})


async def guardian_lift(request: Request):
    body = await _json(request, "payload", "signature")
    uid = _guardian_user(body)
    _desk().sign_lift(uid, body["payload"], body["signature"])
    return JSONResponse({"frozen": runtime.kernel.finish_lift(uid, "", "guardian")})


async def guardian_remove_challenge(request: Request):
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    from app.kernel.signing import build_settings_challenge
    payload = build_settings_challenge(s.session_id, s.user_id, {"remove_guardian": True})
    s.setting_challenges["remove_guardian"] = payload
    return JSONResponse({"payload": payload, "passkey": runtime.kernel.passkey_status(s.user_id, _site(request))})


async def guardian_remove(request: Request):
    """Signed by her, and it only takes effect after a delay, so a scammer on the phone can't rush the guardian away."""
    body = await _json(request)
    s = runtime.sessions.get(body.get("session_id"))
    issued, assertion = s.setting_challenges.get("remove_guardian"), body.get("assertion")
    if not issued or not isinstance(assertion, dict) or assertion.get("payload") != issued:
        return _err("Please confirm this with your fingerprint.", "PAYLOAD_MISMATCH")
    try:
        runtime.kernel._signature(s, issued, assertion, _site(request))  # noqa: SLF001
    except KernelError as e:
        return _err(str(e), e.code)
    s.setting_challenges.pop("remove_guardian", None)
    st = _desk().request_removal(s.user_id, s.session_id)
    runtime.wallet.add_alert(s.user_id, "warn", "guardian", "A request was made to remove the family guardian. It takes effect after a delay.")
    return JSONResponse(st)


# ---- The simulator's own pages: routed only in simulator mode. They are not PayPal and say so loudly.
async def simulator_page(request: Request):
    if not runtime.execution_gateway.simulator or not (ROOT / "web" / "simulator.html").exists():
        return _err("Not found.", "NOT_FOUND", 404)
    return FileResponse(ROOT / "web" / "simulator.html", media_type="text/html; charset=utf-8")


async def simulator_info(request: Request):
    sim = runtime.execution_gateway.simulator
    if not sim:
        return _err("Not found.", "NOT_FOUND", 404)
    q = request.query_params
    if q.get("token") and q["token"] in sim.st["orders"]:
        o = sim.st["orders"][q["token"]]
        unit = o["purchase_units"][0]
        return JSONResponse({"kind": "order", "id": o["id"], "status": o["status"], "description": unit.get("description", ""),
                             "amount": unit["amount"], "items": unit.get("items", []), "simulated": True})
    if q.get("id") and q["id"] in sim.st["invoices"]:
        i = sim.st["invoices"][q["id"]]
        return JSONResponse({"kind": "invoice", "id": i["id"], "status": i["status"], "from": i["invoicer"], "amount": i["amount"],
                             "note": i["detail"].get("note", ""), "simulated": True})
    return _err("Not found.", "NOT_FOUND", 404)


async def simulator_decide(request: Request):
    """The pretend buyer's click. It changes the simulator's state and returns where to go. It sets nothing in Star:
    Star finds out the same way it would from PayPal, by asking and by a signed event."""
    sim = runtime.execution_gateway.simulator
    if not sim:
        return _err("Not found.", "NOT_FOUND", 404)
    body = await _json(request, "kind", "id", "decision")
    try:
        if body["kind"] == "order":
            return JSONResponse({"redirect": sim.buyer_decide_order(body["id"], body["decision"]), "simulated": True})
        if body["kind"] == "invoice":
            if body["decision"] == "pay":
                sim.payer_pay_invoice(body["id"])
            return JSONResponse({"redirect": _return_base(request) + "/", "simulated": True})
        if body["kind"] == "payout" and body["decision"] == "claim":
            sim.claim_payout_item(body["id"])
            return JSONResponse({"redirect": _return_base(request) + "/", "simulated": True})
    except KeyError:
        return _err("Not found.", "NOT_FOUND", 404)
    return _err("Unknown decision.")


routes = [
    Route("/", index),
    Route("/guardian", guardian_page),
    *[Route(path, static) for path in STATIC],
    Route("/api/state", state),
    Route("/api/audit/session/{session_id}", audit_export),
    Route("/api/voice/{session_id}/{sha}", voice_clip),
    Route("/api/health", health),
    Route("/api/chat", chat, methods=["POST"]),
    Route("/api/asr", speech_to_text, methods=["POST"]),
    Route("/api/key", register_key, methods=["POST"]),
    Route("/api/challenge", challenge, methods=["POST"]),
    Route("/api/confirm", confirm, methods=["POST"]),
    Route("/api/passkey/options", passkey_options, methods=["POST"]),
    Route("/api/passkey/register", passkey_register, methods=["POST"]),
    Route("/api/transaction/{session_id}/{draft_id}", transaction_status),
    Route("/api/passkey/remove/challenge", passkey_remove_challenge, methods=["POST"]),
    Route("/api/passkey/remove", passkey_remove, methods=["POST"]),
    Route("/api/protection/challenge", protection_challenge, methods=["POST"]),
    Route("/api/change/challenge", change_challenge, methods=["POST"]),
    Route("/api/change/confirm", change_confirm, methods=["POST"]),
    Route("/api/change/cancel", change_cancel, methods=["POST"]),
    Route("/api/protection", protection_change, methods=["POST"]),
    Route("/api/lab", run_security_lab, methods=["POST"]),
    Route("/api/lab/contract", contract_lab, methods=["POST"]),
    Route("/api/lab/drill", lab_drill, methods=["POST"]),
    Route("/api/lab/naive-agent", lab_naive_agent, methods=["POST"]),
    Route("/api/demo/model-mistake", demo_model_mistake, methods=["POST"]),
    Route("/api/demo/invoice-arrives", demo_invoice_arrives, methods=["POST"]),
    Route("/api/cancel", cancel, methods=["POST"]),
    Route("/api/card/{session_id}", current_card),
    Route("/api/trace/{session_id}", trace),
    Route("/api/audit/verify", audit_verify),
    Route("/api/reset", reset, methods=["POST"]),
    Route("/api/care", care, methods=["POST"]),
    Route("/api/care/offer", care_offer, methods=["POST"]),
    # PayPal
    Route("/api/payments", list_payments),
    Route("/api/payments/{session_id}/{payment_id}", one_payment),
    Route("/api/paypal/orders/return", orders_return, methods=["POST"]),
    Route("/api/paypal/orders/cancelled", orders_cancelled, methods=["POST"]),
    Route("/api/paypal/webhook", paypal_webhook, methods=["POST"]),
    Route("/api/paypal/calls", paypal_calls_log),
    Route("/api/catalogue", catalogue_view),
    Route("/api/invoices", invoices_view),
    Route("/api/disputes", disputes_view),
    Route("/api/activity", activity),
    # Freeze and guardian
    Route("/api/freeze", freeze, methods=["POST"]),
    Route("/api/freeze/lift/challenge", freeze_lift_challenge, methods=["POST"]),
    Route("/api/freeze/lift", freeze_lift, methods=["POST"]),
    Route("/api/guardian/invite/challenge", guardian_invite_challenge, methods=["POST"]),
    Route("/api/guardian/invite", guardian_invite, methods=["POST"]),
    Route("/api/guardian/pair", guardian_pair, methods=["POST"]),
    Route("/api/guardian/feed", guardian_feed, methods=["POST"]),
    Route("/api/guardian/challenge", guardian_challenge, methods=["POST"]),
    Route("/api/guardian/decide", guardian_decide, methods=["POST"]),
    Route("/api/guardian/freeze", guardian_freeze, methods=["POST"]),
    Route("/api/guardian/lift/challenge", guardian_lift_challenge, methods=["POST"]),
    Route("/api/guardian/lift", guardian_lift, methods=["POST"]),
    Route("/api/guardian/remove/challenge", guardian_remove_challenge, methods=["POST"]),
    Route("/api/guardian/remove", guardian_remove, methods=["POST"]),
    # The simulator's own pages
    Route("/simulator/checkout", simulator_page),
    Route("/simulator/invoice", simulator_page),
    Route("/simulator/api/info", simulator_info),
    Route("/simulator/api/decide", simulator_decide, methods=["POST"]),
]

app = Starlette(routes=routes, middleware=[Middleware(SecurityMiddleware)],
                exception_handlers={BadInput: _bad_input, UnknownSession: _unknown_session, GuardianError: _guardian_error})
