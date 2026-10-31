"""Discovery imports this module first, so the whole suite runs on temporary data: no test ever touches data/."""
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="star-tests-")
os.environ.setdefault("LEDGER_PATH", os.path.join(_TMP, "ledger.sqlite3"))
os.environ.setdefault("AUDIT_PATH", os.path.join(_TMP, "audit.jsonl"))
os.environ.setdefault("PASSKEY_PATH", os.path.join(_TMP, "passkeys.json"))
os.environ.setdefault("VOICE_DIR", os.path.join(_TMP, "voice"))
os.environ.setdefault("PAYPAL_MODE", "simulator")
os.environ.setdefault("PAYPAL_SIM_SETTLE_SECONDS", "0")
os.environ.setdefault("LLM_PROVIDER", "offline")
os.environ.setdefault("ASR_PROVIDER", "browser")


class TestIsolation(unittest.TestCase):
    def test_settings_point_at_temporary_data(self):
        from app.config import ROOT, settings
        self.assertNotIn(str(ROOT / "data"), str(settings.ledger_path))
