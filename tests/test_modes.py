"""Modes fail closed: sandbox with no keys, live, or anything else is a readable error and a BLOCK. Never the simulator."""
import os
import unittest
from unittest.mock import patch

from app.paypal import config, factory
from app.paypal.errors import PayPalConfigError
from app.wallet.gateway import ExecutionGateway, GatewayError
from tests.paypal_helpers import World


class TestResolution(unittest.TestCase):
    def test_default_is_the_simulator_and_says_so(self):
        cfg = config.load({})
        self.assertEqual((cfg.mode, cfg.configured, cfg.label), ("simulator", True, "SIMULATED"))
        self.assertTrue(cfg.base_url.endswith(".invalid"))

    def test_sandbox_with_keys(self):
        cfg = config.load({"PAYPAL_MODE": "sandbox", "PAYPAL_CLIENT_ID": "a", "PAYPAL_CLIENT_SECRET": "b"})
        self.assertEqual((cfg.configured, cfg.label, cfg.base_url), (True, "SANDBOX", "https://api-m.sandbox.paypal.com"))

    def test_sandbox_without_keys_names_what_is_missing(self):
        cfg = config.load({"PAYPAL_MODE": "sandbox", "PAYPAL_CLIENT_ID": "a"})
        self.assertFalse(cfg.configured)
        self.assertIn("PAYPAL_CLIENT_SECRET", cfg.error)
        self.assertEqual(cfg.label, "NOT CONFIGURED")

    def test_live_and_garbage_are_refused(self):
        for mode in ("live", "production", "LIVE", "banana"):
            cfg = config.load({"PAYPAL_MODE": mode, "PAYPAL_CLIENT_ID": "a", "PAYPAL_CLIENT_SECRET": "b"})
            self.assertFalse(cfg.configured, mode)
            self.assertIn("Only simulator and sandbox", cfg.error)

    def test_bad_receivers_json_is_an_error(self):
        cfg = config.load({"PAYPAL_MODE": "sandbox", "PAYPAL_CLIENT_ID": "a", "PAYPAL_CLIENT_SECRET": "b", "PAYPAL_SANDBOX_RECEIVERS": "[1]"})
        self.assertFalse(cfg.configured)

    def test_secrets_are_not_in_the_repr(self):
        cfg = config.load({"PAYPAL_MODE": "sandbox", "PAYPAL_CLIENT_ID": "visible-id", "PAYPAL_CLIENT_SECRET": "very-secret"})
        self.assertNotIn("very-secret", repr(cfg))
        self.assertNotIn("very-secret", str(cfg.status()))

    def test_no_simulator_in_sandbox(self):
        pp = factory.build(config.load({"PAYPAL_MODE": "sandbox", "PAYPAL_CLIENT_ID": "a", "PAYPAL_CLIENT_SECRET": "b"}))
        self.assertIsNone(pp.simulator)


class Misconfigured(World):
    ENV = {"PAYPAL_MODE": "sandbox"}

    def setUp(self):
        super().setUp()
        self.paypal = factory.build(config.load(self.ENV), audit=self.audit)
        self.gateway = ExecutionGateway(self.wallet, self.registry, self.audit, paypal=self.paypal)
        self.kernel.gateway = self.gateway
        self.kernel.approvals = self.gateway.approvals

    def test_drafts_are_blocked_with_the_reason(self):
        r = self.turn("Send 50 to my daughter")
        d = self.s.current_draft
        self.assertIn("R09_RAIL_UNAVAILABLE", d.risk.rule_hits)
        self.assertEqual(r.ui["type"], "blocked")
        self.assertIn("PAYPAL_CLIENT_ID", " ".join(d.risk.reasons))

    def test_the_gateway_refuses_even_a_signed_card(self):
        with patch.object(self.gateway, "rail_error", return_value=""):
            self.turn("Send 50 to my daughter")
            d = self.s.current_draft
            ch = self.kernel.issue_challenge(self.s, d.draft_id, d.digest)
            self.kernel.authorize(self.s, d.draft_id, d.digest, self.signature(ch))
        with self.assertRaises(GatewayError) as e:
            self.gateway.execute(user_id="u_lee", session_id=self.s.session_id, card=d.card_fields(), digest=d.digest,
                                 auth=self.s.signatures[d.draft_id])
        self.assertEqual(e.exception.code, "GW_RAIL_UNAVAILABLE")
        self.assertEqual(self.wallet.payments, {})

    def test_reads_raise_a_config_error_without_a_network_call(self):
        with self.assertRaises(PayPalConfigError):
            self.paypal.reader.get_order("A")

    def test_invoice_sync_keeps_the_last_list(self):
        self.assertTrue(self.gateway.invoices.sync("u_lee", force=True))


class TestLive(Misconfigured):
    ENV = {"PAYPAL_MODE": "live", "PAYPAL_CLIENT_ID": "a", "PAYPAL_CLIENT_SECRET": "b"}

    def test_drafts_are_blocked_with_the_reason(self):
        self.turn("Send 50 to my daughter")
        self.assertIn("Only simulator and sandbox", " ".join(self.s.current_draft.risk.reasons))


class TestServerStartsAnyway(unittest.TestCase):
    def test_health_reports_it(self):
        with patch.dict(os.environ, {"PAYPAL_MODE": "sandbox"}):
            cfg = config.load()
        self.assertEqual(cfg.status()["configured"], False)
        self.assertIn("PAYPAL_CLIENT_ID", cfg.status()["error"])
