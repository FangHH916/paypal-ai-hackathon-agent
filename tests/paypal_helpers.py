"""Shared by the PayPal tests: an isolated world, a request stub, and a checker against the official OpenAPI subset."""
from __future__ import annotations

import base64
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from app.agent.llm import OfflineLLM  # noqa: E402
from app.agent.loop import Agent  # noqa: E402
from app.agent.tools import ToolGateway  # noqa: E402
from app.agent.verifier import Verifier  # noqa: E402
from app.kernel import p256  # noqa: E402
from app.kernel.audit import AuditLog  # noqa: E402
from app.kernel.credentials import CredentialRegistry  # noqa: E402
from app.kernel.executor import Kernel  # noqa: E402
from app.kernel.state import Session  # noqa: E402
from app.paypal import factory  # noqa: E402
from app.paypal.config import PayPalConfig  # noqa: E402
from app.wallet.gateway import ExecutionGateway  # noqa: E402
from app.wallet.ledger import Wallet  # noqa: E402

USER = "u_lee"
SUBSET = json.loads((Path(__file__).parent / "fixtures" / "paypal" / "openapi_subset.json").read_text(encoding="utf-8"))


class StubAdapter(HTTPAdapter):
    """Answers from a script of (status, json) or an exception. Records every prepared request."""

    def __init__(self, script=None, default=None):
        super().__init__()
        self.script, self.default, self.seen = list(script or []), default, []

    def send(self, request, **kw):
        self.seen.append(request)
        if request.url.endswith("/v1/oauth2/token"):
            return self._resp(request, 200, {"access_token": f"tok{sum(r.url.endswith('/token') for r in self.seen)}", "expires_in": 3600})
        item = self.script.pop(0) if self.script else self.default
        if isinstance(item, BaseException):
            raise item
        status, body = item
        return self._resp(request, status, body)

    @staticmethod
    def _resp(request, status, body):
        r = requests.Response()
        r.status_code = status
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        r.raw, r._content, r._content_consumed = io.BytesIO(data), data, True
        r.url, r.request = request.url, request
        r.headers["paypal-debug-id"] = "dbg123"
        return r


def sandbox_cfg(**kw) -> PayPalConfig:
    return PayPalConfig(mode="sandbox", client_id="id-secret-value", client_secret="secret-secret-value", **kw)


# ------------------------------------------------------------------------------------ OpenAPI checking
def _resolve(spec, node):
    while isinstance(node, dict) and "$ref" in node:
        cur = spec
        for part in node["$ref"].lstrip("#/").split("/"):
            cur = cur[part]
        node = cur
    return node


def _properties(spec, schema):
    schema = _resolve(spec, schema)
    props, required, extras = {}, list(schema.get("required", [])), []
    for sub in schema.get("allOf", []):
        p, r, e = _properties(spec, sub)
        props.update(p)
        required += r
        extras += e
    props.update(schema.get("properties", {}))
    extras += [schema] if "additionalProperties" in schema else []
    return props, required, extras


def check(spec, schema, value, where="$", problems=None, strict_required=True):
    """A small validator: unknown fields, required fields, enums, length limits, types. Returns a list of problems."""
    problems = [] if problems is None else problems
    schema = _resolve(spec, schema)
    if isinstance(value, dict):
        props, required, extras = _properties(spec, schema)
        if props or required:
            for k, v in value.items():
                if k.startswith("_"):
                    continue
                if k not in props and not extras:
                    problems.append(f"{where}.{k}: not in the spec")
                elif k in props:
                    check(spec, props[k], v, f"{where}.{k}", problems, strict_required)
            if strict_required:
                for k in required:
                    if k not in value:
                        problems.append(f"{where}.{k}: required by the spec")
    elif isinstance(value, list):
        items = schema.get("items")
        if items:
            for i, v in enumerate(value):
                check(spec, items, v, f"{where}[{i}]", problems, strict_required)
    elif isinstance(value, str):
        if "enum" in schema and value not in schema["enum"]:
            problems.append(f"{where}: {value!r} not in {schema['enum']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            problems.append(f"{where}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            problems.append(f"{where}: {value!r} doesn't match {schema['pattern']}")
    return problems


def find_operation(method: str, path: str):
    """(spec, operation, template) for a real request path."""
    for spec in SUBSET["apis"].values():
        for template, ops in spec["paths"].items():
            if method.lower() in ops and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path):
                return spec, ops[method.lower()], template
    return None


def body_schema(op) -> dict | None:
    return ((op.get("requestBody") or {}).get("content", {}).get("application/json") or {}).get("schema")


def response_schema(spec, op, status: int) -> dict | None:
    resp = _resolve(spec, op["responses"].get(str(status)) or {})
    return (resp.get("content", {}).get("application/json") or {}).get("schema")


def request_problems(method: str, path: str, body: bytes, headers: dict, query: dict | None = None) -> list[str]:
    found = find_operation(method, path)
    if not found:
        return [f"{method} {path}: not in the OpenAPI subset"]
    spec, op, _ = found
    problems = []
    schema = body_schema(op)
    if body and schema:
        check(spec, schema, json.loads(body), "body", problems)
    params = [_resolve(spec, p) for p in op.get("parameters", [])]
    for p in params:
        if p.get("in") == "header" and p["name"].lower() in {k.lower() for k in headers}:
            val = next(v for k, v in headers.items() if k.lower() == p["name"].lower())
            check(spec, p.get("schema", {}), val, f"header {p['name']}", problems)
    if query:
        names = {p["name"] for p in params if p.get("in") == "query"}
        problems += [f"query {k}: not in the spec" for k in query if k not in names]
    return problems


# ------------------------------------------------------------------------------------ a whole world
class World(unittest.TestCase):
    """A fresh wallet, simulator, kernel and agent: the real code end to end, keyless, isolated from everything."""
    settle = 0.0

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wallet = Wallet()
        self.audit = AuditLog(os.path.join(self.tmp.name, "audit.jsonl"))
        self.paypal = factory.build(PayPalConfig(), audit=self.audit, settle_seconds=self.settle)
        self.sim = self.paypal.simulator
        self.registry = CredentialRegistry()
        self.registry.allow_browser_key = True
        self.gateway = ExecutionGateway(self.wallet, self.registry, self.audit, paypal=self.paypal)
        self.kernel = Kernel(self.wallet, self.audit, allow_mock=False, registry=self.registry, gateway=self.gateway)
        self.tools = ToolGateway(self.wallet, self.kernel, verifier=Verifier(None, self.wallet))
        self.agent = Agent(self.wallet, self.tools, self.audit, OfflineLLM())
        self.s = Session(session_id="world")
        self.key, jwk = p256.generate_keypair()
        self.kernel.register_key(self.s, jwk)
        self.rec_mod = self.gateway.reconciler

    def turn(self, text):
        return self.agent.run_turn(self.s, text)

    def signature(self, payload: str, key=None) -> dict:
        return {"payload": payload, "signature": base64.b64encode(p256.sign(key or self.key, payload.encode())).decode()}

    def sign(self, d=None, return_base="https://star.example"):
        """What the confirmation card does: answer the question, wait out the cooling, sign, commit."""
        d = d or self.s.current_draft
        if d.cooling_until:
            d.cooling_until = 0
            self.kernel._refresh_cooling(self.s, d)
        ans = True if d.risk.verify_question else None
        ch = self.kernel.issue_challenge(self.s, d.draft_id, d.digest, verify_answer=ans)
        tok = self.kernel.authorize(self.s, d.draft_id, d.digest, self.signature(ch), verify_answer=ans)
        r = self.kernel.commit(self.s, d.draft_id, tok, return_base=return_base)
        self.s.settle()
        return r

    def rec(self, receipt) -> dict:
        return self.wallet.payment(receipt["payment_id"])

    def pump(self):
        self.rec_mod.pump()

    def writes(self, path_end=""):
        return [r for r in self.sim.requests_seen if r["method"] == "POST" and r["path"].endswith(path_end)
                and r["path"] not in ("/v1/oauth2/token", "/v2/invoicing/search-invoices", "/v1/notifications/verify-webhook-signature")]

    def buy(self, text="Get me a blood pressure monitor"):
        self.turn(text)
        receipt = self.sign()
        rec = self.rec(receipt)
        self.sim.buyer_decide_order(rec["paypal"]["order_id"], "approve")
        self.rec_mod.order_return(rec)
        return rec

    def pair_guardian(self):
        token = self.gateway.guardian.invite(USER)
        key, jwk = p256.generate_keypair()
        self.guardian_token = self.gateway.guardian.pair(token, jwk)["token"]
        self.guardian_key = key
        return key

    def guardian_decides(self, draft, decision="approve", key=None):
        desk = self.gateway.guardian
        payload = desk.challenge(USER, draft.draft_id, decision)
        row = desk.decide(USER, payload, self.signature(payload, key or self.guardian_key)["signature"])
        if decision == "approve":
            self.kernel.approve_guardian(self.s, draft.draft_id, draft.digest, row["signed"])
        else:
            self.kernel.deny_guardian(self.s, draft.draft_id, draft.digest, row["signed"])
        return row
