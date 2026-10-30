"""One authenticated HTTP path to PayPal: token cache, timeouts, error mapping and the call log.

No automatic retries, ever, except the single 401 token refresh with the same idempotency key. Whether a failed write
may be repeated is decided by reconcile.py after querying, never here.
"""
from __future__ import annotations

import json
import threading
import time

import requests
from requests.adapters import HTTPAdapter

from app.paypal import calls
from app.paypal.config import PayPalConfig
from app.paypal.errors import PayPalConfigError, PayPalError, PayPalUnknown

TOKEN_MARGIN = 60          # refresh this many seconds before expires_in says it runs out


class PayPalTransport:
    def __init__(self, cfg: PayPalConfig, *, adapter: HTTPAdapter | None = None, log: calls.CallLog | None = None):
        self.cfg, self.log = cfg, log or calls.CallLog()
        self.session = requests.Session()
        self.adapter = adapter or HTTPAdapter(max_retries=0)
        self.session.mount("https://", self.adapter)
        self.session.mount("http://", self.adapter)
        self._lock = threading.Lock()
        self._token, self._expires_at = "", 0.0

    # ---------------------------------------------------------------- token
    def _get_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at:
                return self._token
            r = self._send("oauth", "POST", "/v1/oauth2/token", "/v1/oauth2/token", write=False,
                           data={"grant_type": "client_credentials"}, auth=(self.cfg.client_id, self.cfg.client_secret),
                           headers={"Accept": "application/json"})
            if r.status_code >= 500:
                raise PayPalUnknown(f"PayPal answered {r.status_code}", r.status_code)
            if r.status_code != 200:
                raise PayPalError(r.status_code, *self._error_fields(r))
            try:
                body = r.json()
                self._token = body["access_token"]
                self._expires_at = time.time() + float(body.get("expires_in", 0)) - TOKEN_MARGIN
            except (ValueError, KeyError, TypeError) as e:
                raise PayPalUnknown("Unreadable token response") from e
            return self._token

    def drop_token(self) -> None:
        with self._lock:
            self._token, self._expires_at = "", 0.0

    # ---------------------------------------------------------------- request
    def request(self, op: str, method: str, template: str, *, path: str | None = None, body: dict | None = None,
                raw_body: bytes | None = None, headers: dict | None = None, params: dict | None = None,
                write: bool = False) -> dict:
        if not self.cfg.configured:
            raise PayPalConfigError(self.cfg.error)
        path = path or template
        hdrs = {"Accept": "application/json", **(headers or {})}
        data = None
        if raw_body is not None:
            data = raw_body
            hdrs["Content-Type"] = "application/json"
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            hdrs["Content-Type"] = "application/json"
        r = None
        for attempt in (1, 2):
            hdrs["Authorization"] = "Bearer " + self._get_token()
            r = self._send(op, method, template, path, write=write, data=data, headers=hdrs, params=params)
            if r.status_code == 401 and attempt == 1:     # token expired early: one refresh, SAME idempotency key
                self.drop_token()
                continue
            break
        if r.status_code >= 500:
            raise PayPalUnknown(f"PayPal answered {r.status_code}", r.status_code, r.headers.get("paypal-debug-id", ""))
        if r.status_code >= 400:
            raise PayPalError(r.status_code, *self._error_fields(r))
        if r.status_code == 204 or not r.content:
            return {"_status": r.status_code}
        try:
            out = r.json()
        except ValueError as e:
            raise PayPalUnknown("PayPal answered but the body could not be read", r.status_code) from e
        if not isinstance(out, dict):
            raise PayPalUnknown("PayPal answered with an unexpected body", r.status_code)
        out["_status"] = r.status_code
        return out

    def _send(self, op, method, template, path, *, write, **kw) -> requests.Response:
        sc = calls.current()
        t0 = time.time()
        url = self.cfg.base_url + path
        try:
            r = self.session.request(method, url, timeout=(self.cfg.connect_timeout, self.cfg.timeout),
                                     allow_redirects=False, **kw)
        except requests.RequestException as e:
            self._record(sc, op, method, template, None, t0, "", write, "unknown")
            # Keep the exception's text (which holds URLs) out of anything shown or logged.
            raise PayPalUnknown(f"No answer from PayPal ({type(e).__name__})") from None
        dbg = r.headers.get("paypal-debug-id", "")
        outcome = "ok" if r.status_code < 400 else "unknown" if r.status_code >= 500 else "rejected"
        self._record(sc, op, method, template, r.status_code, t0, dbg, write, outcome)
        return r

    def _record(self, sc, op, method, template, status, t0, dbg, write, outcome):
        self.log.record(calls.CallRecord(at=time.time(), op=op, method=method, path=template, status=status,
                                         ms=int((time.time() - t0) * 1000), debug_id=dbg, mode=self.cfg.mode,
                                         write=write, outcome=outcome, user_id=sc.user_id, session_id=sc.session_id,
                                         payment_id=sc.payment_id))

    @staticmethod
    def _error_fields(r) -> tuple:
        try:
            e = r.json()
            if not isinstance(e, dict):
                raise ValueError
        except ValueError:
            return ("", "", r.headers.get("paypal-debug-id", ""), [], [])
        # The OAuth endpoint uses {"error", "error_description"}; everything else {"name", "message", "debug_id"}.
        return (str(e.get("name") or e.get("error") or ""), str(e.get("message") or e.get("error_description") or ""),
                str(e.get("debug_id") or r.headers.get("paypal-debug-id", "")),
                e.get("links") if isinstance(e.get("links"), list) else [],
                e.get("details") if isinstance(e.get("details"), list) else [])
