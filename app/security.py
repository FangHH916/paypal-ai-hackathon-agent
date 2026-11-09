"""HTTP hardening for the demo server (adapted from a teammate's prototype, Aegis).

* Host allowlist      - only answer to localhost (or ALLOWED_HOSTS): blocks DNS-rebinding attacks
* Same-origin POSTs   - a browser POST with a foreign Origin header is refused: blocks cross-site requests
* JSON only           - POST bodies must be application/json: a plain HTML form can't trigger an API call
* Size limits         - 32 KB for API calls, 64 KB for the webhook, 3 MB for the speech endpoint
* Rate limits         - online demo only (PUBLIC_DEMO=1): model, speech, lab and reset calls per address per 5 minutes;
                        the webhook and guardian endpoints always, and a guardian's feed per device as well
* Security headers    - strict CSP (no inline script, no third-party code), no framing, no sniffing,
                        microphone allowed only for this page

PayPal's webhook POST has no Origin header, so the same-origin rule passes it; only its verified signature counts.
This is a single-process demo server. Do not expose it to the internet as-is (see docs/SECURITY.md).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import deque

from app.config import settings

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; "
       "media-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
# AG Grid writes inline styles, so pages that show the grid get a per-response nonce plus these hashes. The script policy
# never changes. Hashes pinned for AG Grid Community 36.2.0, captured from real Edge runs: re-capture them whenever
# web/vendor/ag-grid is updated (qa/e2e_demo.py fails on any CSP violation, which catches drift).
GRID_STYLE_HASHES: tuple[str, ...] = (
    "sha256-d0lJUpvoC2AtY1hfNkYtQ3PTwyHQPioPc8nziQ4oZnI=", "sha256-rQlkWiJKE/MPGpwDe69lvmDFJRUg9V88F/ZeiWtTua0=",
    "sha256-oXAVhm/Tmpemvg4pcBJYYAks/omJTSfcyO5A4dtV1VA=", "sha256-N9lQmoTBNVuB9veR0ObhN5kBeM45U2ObJtzGeUlbsaU=",
    "sha256-6m0aDqwUs2k+1epVPH0ywm4WKo7YiYU2NgwB3DT9gMc=", "sha256-dtD/sM2v5XlIn1wPYyUEm43xidPdaI0LF4PajtmJiSE=",
    "sha256-vPdwiprKoXKg1P/2L54QAIcS+CWfQAE/S6dhZDsBI3o=", "sha256-ThSUXmROfoNAIvSF4+uzed2+dZV0uXiqvHSgMxUl2VE=",
    "sha256-+JAU1ADRr5m4X8K1hi1NIH2tj4DgFFXD8S67RY4KM+s=", "sha256-aXKz0aUlehLPnZcEUwqL32uuvVpsAEJcnJ4Jtx1ZrDM=",
    "sha256-cNiyk3T2wgewzNCZqAIpEXsfcOcxlj47HLSXcOfZdm0=", "sha256-fHF/8NDSsl9iq2ju6JN1VXolna7LUFRnPKsAsAXKwRU=",
    "sha256-ca2uO9X/1nVOZdm86QHPggbRkppfti9qReD+I87mzh4=", "sha256-S+wf3bS9OU4MKKlzUQCDrmGl48WJ8KRt0aZuzxJsenQ=",
)


def grid_csp(nonce: str) -> str:
    hashes = "".join(f" '{h}'" for h in GRID_STYLE_HASHES)
    unsafe = " 'unsafe-hashes'" if GRID_STYLE_HASHES else ""
    return (f"default-src 'self'; script-src 'self'; style-src 'self' 'nonce-{nonce}'{unsafe}{hashes}; img-src 'self' data: blob:; "
            "font-src 'self' data:; connect-src 'self'; media-src 'self' blob:; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'")


# The Dashboard runs AG Studio in a same-origin frame of its own. AG Studio writes many inline style attributes whose
# values change with the data, so they can't be pinned by hash like the grid's. Only this frame allows inline styles;
# its scripts are still only this origin's, it can only be framed by this origin, and the app around it keeps the strict
# policy above. The frame only reads the activity rows; nothing in it can move money.
DASHBOARD_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                 "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'; "
                 "form-action 'none'")


HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), geolocation=(), microphone=(self)"),
    (b"cache-control", b"no-store"),
]
API_LIMIT, ASR_LIMIT, WEBHOOK_LIMIT = 32 * 1024, 3 * 1024 * 1024, 64 * 1024   # official webhook max size: unverified
LIMITS = {"/api/asr": ASR_LIMIT, "/api/paypal/webhook": WEBHOOK_LIMIT}


# Each device gets a fair share of the model and speech APIs (they cost money and have quotas)
COSTLY = {"/api/chat": 40, "/api/asr": 20, "/api/lab": 12, "/api/lab/contract": 6, "/api/lab/drill": 12, "/api/lab/naive-agent": 12,
          "/api/demo/model-mistake": 12, "/api/reset": 30}          # requests per address per 5 minutes
# These answer to anyone on the internet, so they are limited in every mode
ALWAYS = {"/api/paypal/webhook": 600, "/api/guardian/pair": 30, "/api/guardian/feed": 2400, "/api/guardian/challenge": 60,
          "/api/guardian/decide": 60, "/api/guardian/freeze": 20, "/api/guardian/lift": 20, "/simulator/api/decide": 60}
FEED_PER_DEVICE = 400         # a guardian page polls every 3 s. Several family devices can share one address.
_WINDOW = 300
_hits: dict[tuple[str, str], deque] = {}
_hits_lock = threading.Lock()


def _client(scope, headers) -> str:
    # Never let arbitrary forwarded headers create a fresh rate-limit identity: only the entries our own proxies appended
    # count, and everything to their left is forgeable. Without TRUSTED_PROXY_HOPS the header is ignored altogether, and
    # behind a proxy all clients share one address. The ASGI server must run with proxy_headers=False (see app/serve.py).
    peer = (scope.get("client") or ("?",))[0]
    hops = settings.trusted_proxy_hops
    chain = [a.strip() for a in headers.get("x-forwarded-for", "").split(",") if a.strip()]
    return chain[-hops] if 0 < hops <= len(chain) else peer


def _allow(client: str, path: str, limit: int | None = None) -> bool:
    now = time.time()
    with _hits_lock:
        if len(_hits) > 20000:
            _hits.clear()
        q = _hits.setdefault((client, path), deque())
        while q and now - q[0] > _WINDOW:
            q.popleft()
        if len(q) >= (limit if limit is not None else {**COSTLY, **ALWAYS}[path]):
            return False
        q.append(now)
        return True


def _feed_device(body: bytes) -> str:
    try:
        token = json.loads(body).get("token")
    except (ValueError, AttributeError):
        token = None
    return hashlib.sha256(str(token).encode()).hexdigest()


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        host = headers.get("host", "")
        hostname = host.rsplit(":", 1)[0].lower() if not host.startswith("[") else host.split("]")[0] + "]"
        path, method = scope["path"], scope["method"]

        if hostname not in settings.allowed_hosts:
            return await self._deny(send, 403, "INVALID_HOST",
                                    "Open the app via localhost (or add this host to ALLOWED_HOSTS).")
        if method == "POST":
            origin = headers.get("origin")
            if origin and origin not in (f"http://{host}", f"https://{host}"):
                return await self._deny(send, 403, "ORIGIN", "Cross-site requests are not allowed.")
            if not headers.get("content-type", "").startswith("application/json"):
                return await self._deny(send, 415, "CONTENT_TYPE", "JSON is required.")
            limit = LIMITS.get(path, API_LIMIT)
            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                length = limit + 1
            if length < 0 or length > limit:
                return await self._deny(send, 413, "TOO_LARGE", "Request is too large.")
            limited = path in ALWAYS or (settings.public_demo and path in COSTLY)
            if limited and not _allow(_client(scope, headers), path):
                return await self._deny(send, 429, "RATE_LIMITED",
                                        "Too many requests from this device. Please wait a minute and try again.")

        if method == "POST":
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > limit:
                    return await self._deny(send, 413, "TOO_LARGE", "Request is too large.")
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            if path == "/api/guardian/feed" and not _allow("device:" + _feed_device(b"".join(chunks)), path, FEED_PER_DEVICE):
                return await self._deny(send, 429, "RATE_LIMITED", "Too many requests from this device. Please wait a minute and try again.")
            original_receive = receive
            delivered = False
            async def buffered_receive():
                nonlocal delivered
                if delivered:
                    return await original_receive()
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            receive = buffered_receive

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [(k, v) for k, v in HEADERS if k not in existing]
            await send(message)

        await self.app(scope, receive, send_with_headers)

    @staticmethod
    async def _deny(send, status: int, code: str, msg: str):
        body = json.dumps({"error": msg, "code": code}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                *HEADERS]})
        await send({"type": "http.response.body", "body": body})
