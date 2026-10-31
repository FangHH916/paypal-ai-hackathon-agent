"""Issue the demo's invoices in the real PayPal sandbox, from the Household business account, so bills can "arrive".

    PAYPAL_MODE=sandbox PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... python scripts/paypal_seed_sandbox.py buyer@personal.example

The argument is the sandbox personal account of Mrs. Lee. Each invoice carries the reference tag "star-demo", which is how
Star finds them. Safe to run twice: an invoice number already there is skipped. Not yet run against the real sandbox.
Limitation, stated plainly: PayPal REST gives invoice lists to the issuer, not the payer, so in this demo the Household
account issues the bills, under the names of fictional billers.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.paypal import config, factory  # noqa: E402
from app.paypal.errors import PayPalError, PayPalUnknown  # noqa: E402
from app.wallet.catalogue import FAKE_INVOICE, INVOICE_SEEDS  # noqa: E402
from app.wallet.invoices import SANDBOX_TAG  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    cfg = config.load()
    if cfg.mode != "sandbox" or not cfg.configured:
        print(f"Needs PAYPAL_MODE=sandbox and keys. {cfg.error}")
        return 2
    client = factory.build(cfg).client
    failed = 0
    for seed in [*INVOICE_SEEDS, FAKE_INVOICE]:
        try:
            if client.search_invoices({"invoice_number": seed["number"]}).get("items"):
                print(f"SKIP {seed['number']}: already there")
                continue
            r = client.create_invoice(number=seed["number"], currency=cfg_currency(), recipient_email=sys.argv[1], item_name=seed["title"],
                                      amount_cents=seed["amount_cents"], note=seed["note"], terms=seed["terms"], reference=SANDBOX_TAG,
                                      invoicer_name=seed["invoicer_name"], due_date=seed["due"])
            client.send_invoice(r.get("id") or r.get("href", "").rsplit("/", 1)[-1])
            print(f"SENT {seed['number']}: {seed['title']}")
        except (PayPalError, PayPalUnknown) as e:
            failed += 1
            print(f"FAIL {seed['number']}: {e}")
    return 1 if failed else 0


def cfg_currency() -> str:
    from app.config import settings
    return settings.wallet_currency


if __name__ == "__main__":
    sys.exit(main())
