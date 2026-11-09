"""The Postman files stay importable and never carry a key."""
import json
import unittest
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "postman"


class TestPostman(unittest.TestCase):
    def test_collection_parses_and_covers_the_transfer_calls(self):
        col = json.loads((DIR / "star-transfer.postman_collection.json").read_text(encoding="utf-8"))
        urls = json.dumps(col)
        for path in ("/v1/oauth2/token", "/v1/payments/payouts", "/v1/payments/payouts-item/", "/api/confirm"):
            self.assertIn(path, urls)
        self.assertNotIn("api-m.paypal.com", urls)          # sandbox only, never live

    def test_environment_holds_no_secret(self):
        env = json.loads((DIR / "star-sandbox.postman_environment.json").read_text(encoding="utf-8"))
        for v in env["values"]:
            if v["key"] in ("client_id", "client_secret", "access_token"):
                self.assertEqual(v["value"], "", v["key"])
        self.assertEqual(next(v["value"] for v in env["values"] if v["key"] == "paypal_base"), "https://api-m.sandbox.paypal.com")


if __name__ == "__main__":
    unittest.main()
