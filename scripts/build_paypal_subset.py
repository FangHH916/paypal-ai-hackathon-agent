"""Build tests/fixtures/paypal/openapi_subset.json from PayPal's official OpenAPI files.

    python scripts/build_paypal_subset.py <folder with the official JSON files>

Source: https://github.com/paypal/paypal-rest-api-specifications (openapi/*.json, Apache-2.0). The subset keeps only the
operations Star uses and the schemas they reach, with descriptions stripped, so tests can check every request and response
field against the spec without carrying 3 MB of JSON. Fetched for this repo on 2026-10-30, from the main branch.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "paypal" / "openapi_subset.json"
OPERATIONS = {
    "payments_payouts_batch_v1.json": [("post", "/v1/payments/payouts"), ("get", "/v1/payments/payouts/{id}"),
                                       ("get", "/v1/payments/payouts-item/{payout_item_id}"),
                                       ("post", "/v1/payments/payouts-item/{payout_item_id}/cancel")],
    "checkout_orders_v2.json": [("post", "/v2/checkout/orders"), ("get", "/v2/checkout/orders/{id}"),
                                ("post", "/v2/checkout/orders/{id}/capture")],
    "payments_payment_v2.json": [("get", "/v2/payments/captures/{capture_id}"), ("post", "/v2/payments/captures/{capture_id}/refund"),
                                 ("get", "/v2/payments/refunds/{refund_id}")],
    "invoicing_v2.json": [("post", "/v2/invoicing/invoices"), ("get", "/v2/invoicing/invoices/{invoice_id}"),
                          ("post", "/v2/invoicing/invoices/{invoice_id}/send"), ("post", "/v2/invoicing/search-invoices")],
    "notifications_webhooks_v1.json": [("post", "/v1/notifications/verify-webhook-signature"), ("get", "/v1/notifications/webhooks")],
    "customer_disputes_v1.json": [("get", "/v1/customer/disputes"), ("get", "/v1/customer/disputes/{id}")],
    "reporting_transactions_v1.json": [("get", "/v1/reporting/transactions")],
}
DROP = {"description", "title", "x-enumDescriptions", "examples", "example", "summary", "externalDocs", "x-apidog-orders"}


def strip(node, names=False):
    """names=True: this dict's keys are field names (under "properties"), so a field called "description" stays."""
    if isinstance(node, dict):
        return {k: strip(v, k == "properties" and not names) for k, v in node.items() if names or k not in DROP}
    if isinstance(node, list):
        return [strip(x) for x in node]
    return node


def refs(node):
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            yield node["$ref"]
        for v in node.values():
            yield from refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from refs(v)


def lookup(spec, ref):
    node = spec
    for part in ref.lstrip("#/").split("/"):
        node = node[part.replace("~1", "/")]
    return node


def build(folder: Path) -> dict:
    out = {"source": "github.com/paypal/paypal-rest-api-specifications (Apache-2.0)", "fetched": "2026-10-30", "apis": {}}
    for name, ops in OPERATIONS.items():
        spec = json.loads((folder / name).read_text(encoding="utf-8"))
        kept = {"paths": {}, "components": {}}
        pending = []
        for method, path in ops:
            op = spec["paths"][path][method]
            kept["paths"].setdefault(path, {})[method] = strip({k: op[k] for k in ("parameters", "requestBody", "responses") if k in op})
            pending += list(refs(kept["paths"][path][method]))
        seen = set()
        while pending:
            ref = pending.pop()
            if ref in seen or not ref.startswith("#/"):
                continue
            seen.add(ref)
            parts = ref.lstrip("#/").split("/")
            node = strip(lookup(spec, ref))
            target = kept
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = node
            pending += list(refs(node))
        out["apis"][name] = kept
    return out


if __name__ == "__main__":
    folder = Path(sys.argv[1])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(folder), separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
