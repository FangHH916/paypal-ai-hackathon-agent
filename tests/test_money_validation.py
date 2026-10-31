"""A provider's signed success must match the card's exact amount and currency."""
import copy
import unittest
from app.paypal.reconcile import _cents
from tests.paypal_helpers import World


class MoneyParsing(unittest.TestCase):
    def test_money_never_rounds_extra_decimals_or_accepts_nonfinite_values(self):
        for text in ("1.005", "NaN", "Infinity", "-1.00", "1e2", " 1.00", 1.0, None):
            self.assertIsNone(_cents(text), text)
        self.assertEqual(_cents("1.01"), 101)
        self.assertEqual(_cents("50"), 5000)


class CurrencyChecks(World):
    def test_capture_refund_and_invoice_with_wrong_currency_are_anomalies(self):
        self.turn("Send 50 to my daughter")
        original = self.rec(self.sign())
        for kind in ("capture", "refund", "invoice"):
            with self.subTest(kind=kind):
                rec = copy.deepcopy(original)
                rec["state"], rec["verified"] = {"capture": "AWAITING_APPROVAL", "refund": "REFUND_PENDING", "invoice": "AWAITING_PAYMENT"}[kind], False
                self.wallet.payments[rec["payment_id"]] = rec
                body = {"status": "PAID" if kind == "invoice" else "COMPLETED", "amount": {"value": "50.00", "currency_code": "EUR"}}
                getattr(self.rec_mod, "apply_" + kind)(rec, body, "get:synthetic")
                self.assertEqual((rec["state"], rec["verified"]), ("ANOMALY", False))
