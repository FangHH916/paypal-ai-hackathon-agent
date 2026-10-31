"""Bills arrive as PayPal invoices. This keeps the household's list in step with what PayPal says.

The same code path in both modes: POST /v2/invoicing/search-invoices through the read-only reader, filtered by a reference
tag. In the simulator each visitor's invoices are seeded under their own tag. In the sandbox scripts/paypal_seed_sandbox.py
issues them from the Household account under the tag "star-demo". Stated limitation: PayPal REST gives invoice lists to
the issuer, not the payer, so a production version would need an inbox integration (docs/PAYPAL_NOTES.md).
"""
from __future__ import annotations

import re
import time

from app.paypal.errors import PayPalConfigError, PayPalError, PayPalUnknown
from app.wallet import catalogue
from app.wallet.ledger import _seed_invoice

SANDBOX_TAG = "star-demo"
SYNC_EVERY = 3.0


def _cents(value) -> int:
    return int(round(float(value) * 100))


class InvoiceDesk:
    def __init__(self, wallet, reader, simulator=None, audit=None):
        self.wallet, self.reader, self.simulator, self.audit = wallet, reader, simulator, audit
        self._last: dict[str, float] = {}

    def tag(self, user_id: str) -> str:
        return self.wallet.user(user_id)["invoice_tag"] if self.simulator else SANDBOX_TAG

    def _seed(self, user_id: str, row: dict) -> None:
        self.simulator.seed_invoice(number=row["number"], invoicer_name=row["invoicer_name"], invoicer_email=row["invoicer_email"],
                                    item_name=row["title"], amount_cents=row["amount_cents"], currency=self.wallet.accounts(user_id)[0]["currency"],
                                    note=row["note"], terms=row["terms"], due=row["due"], reference=self.tag(user_id))

    def sync(self, user_id: str, force: bool = False) -> list[dict]:
        """Bring the list up to date. Never raises: if PayPal can't be reached, the last known list stays."""
        now = time.time()
        if not force and now - self._last.get(user_id, 0) < SYNC_EVERY:
            return self.wallet.invoices(user_id, only_open=False)
        self._last[user_id] = now
        if self.simulator:
            for row in self.wallet.invoices(user_id, only_open=False):
                if not row["paypal_id"]:
                    self._seed(user_id, row)
        try:
            found = self.reader.search_invoices({"reference": self.tag(user_id)}).get("items") or []
        except (PayPalUnknown, PayPalError, PayPalConfigError):
            return self.wallet.invoices(user_id, only_open=False)
        self.wallet.update_user(user_id, lambda u: self._merge(u, found))
        return self.wallet.invoices(user_id, only_open=False)

    def _merge(self, user: dict, found: list[dict]) -> None:
        if not self.simulator:              # in the sandbox the list is what PayPal has, nothing from the demo seeds
            user["invoices"][:] = [r for r in user["invoices"] if r["paypal_id"]]
        rows = {r["number"]: r for r in user["invoices"]}
        for inv in found:
            number = (inv.get("detail") or {}).get("invoice_number", "")
            if not number:
                continue
            row = rows.get(number)
            if row is None:
                row = self._new_row(user, inv, number)
                user["invoices"].append(row)
                rows[number] = row
            detail = inv.get("detail") or {}
            row.update(paypal_id=inv.get("id", ""), status=inv.get("status", row["status"]),
                       note=detail.get("note", row["note"]), terms=detail.get("terms_and_conditions", row["terms"]),
                       view_url=(detail.get("metadata") or {}).get("recipient_view_url", ""))
            amount = (inv.get("amount") or {}).get("value")
            if amount is not None and row["status"] in ("SENT", "UNPAID"):
                row["amount_cents"] = _cents(amount)

    @staticmethod
    def _new_row(user: dict, inv: dict, number: str) -> dict:
        """An invoice nobody seeded locally. Who it claims to be from is only a claim: it's matched against saved billers
        by exact name, and anything else is an unknown sender (rule R82)."""
        who = (inv.get("invoicer") or {})
        name = who.get("business_name") or "Unknown sender"
        saved = next((p for p in user["payees"] if p["type"] == "biller" and p["name"].lower() == name.lower()), None)
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        detail = inv.get("detail") or {}
        title = ((inv.get("items") or [{}])[0].get("name")) or f"Invoice {number}"
        return {**_seed_invoice({"invoice_id": "inv_" + re.sub(r"[^a-z0-9]+", "_", number.lower()), "number": number,
                                 "payee_id": saved["payee_id"] if saved else f"invoicer:{slug}", "title": title,
                                 "invoicer_name": name, "invoicer_email": who.get("email_address", ""),
                                 "amount_cents": _cents((inv.get("amount") or {}).get("value", 0)),
                                 "due": (detail.get("payment_term") or {}).get("due_date", ""), "note": "", "terms": ""})}

    def arrive_fake(self, user_id: str) -> dict | None:
        """The demo's "New invoice arrives" control. Simulator only: in the sandbox the seed script issues it."""
        if not self.simulator:
            return None
        if not self.wallet.invoice(user_id, catalogue.FAKE_INVOICE["invoice_id"]):
            row = _seed_invoice(catalogue.FAKE_INVOICE)
            self.wallet.update_user(user_id, lambda u: u["invoices"].append(row))
            self._seed(user_id, row)
        self.sync(user_id, force=True)
        return self.wallet.invoice(user_id, catalogue.FAKE_INVOICE["invoice_id"])
