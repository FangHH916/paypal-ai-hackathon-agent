"""PayPal mode resolution. Fail closed: no credentials or an unknown mode means nothing executes, and the app says why.
There is deliberately no live base URL anywhere in the code and no fallback from sandbox to the simulator."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
SIMULATOR_BASE = "https://paypal-simulator.invalid"      # .invalid can never resolve, so a bug can't reach a network
APPROVAL_HOST_SUFFIX = ".sandbox.paypal.com"


@dataclass(frozen=True)
class PayPalConfig:
    mode: str = "simulator"                 # simulator | sandbox | the invalid value that was given
    client_id: str = field(default="", repr=False)
    client_secret: str = field(default="", repr=False)
    webhook_id: str = ""
    receivers: dict = field(default_factory=dict, repr=False)   # sandbox: payee id -> sandbox personal account email
    timeout: float = 20.0
    connect_timeout: float = 5.0
    error: str = ""                         # empty = configured

    @property
    def configured(self) -> bool:
        return not self.error

    @property
    def base_url(self) -> str:
        return SANDBOX_BASE if self.mode == "sandbox" else SIMULATOR_BASE

    @property
    def label(self) -> str:
        """The tag on every PayPal status in the UI."""
        return {"simulator": "SIMULATED", "sandbox": "SANDBOX"}.get(self.mode, "NOT CONFIGURED") if self.configured \
            else "NOT CONFIGURED"

    def status(self) -> dict:
        webhooks = ("simulated, signature-checked" if self.mode == "simulator" else
                    "signature-verified" if self.webhook_id else "not configured, using PayPal status checks")
        return {"mode": self.mode, "configured": self.configured, "label": self.label, "error": self.error,
                "webhooks": webhooks}


def load(env=None) -> PayPalConfig:
    env = os.environ if env is None else env
    mode = (env.get("PAYPAL_MODE") or "simulator").strip().lower()
    cid, secret = (env.get("PAYPAL_CLIENT_ID") or "").strip(), (env.get("PAYPAL_CLIENT_SECRET") or "").strip()
    webhook = (env.get("PAYPAL_WEBHOOK_ID") or "").strip()
    timeout = float(env.get("PAYPAL_TIMEOUT") or 20)
    error = ""
    receivers: dict = {}
    if mode not in ("simulator", "sandbox"):
        error = f"PAYPAL_MODE={mode!r} is not supported. Only simulator and sandbox are supported."
    elif mode == "sandbox":
        missing = [n for n, v in (("PAYPAL_CLIENT_ID", cid), ("PAYPAL_CLIENT_SECRET", secret)) if not v]
        if missing:
            error = f"PAYPAL_MODE=sandbox needs {' and '.join(missing)}. Nothing will be sent to PayPal until they are set."
        try:
            receivers = json.loads(env.get("PAYPAL_SANDBOX_RECEIVERS") or "{}")
            if not isinstance(receivers, dict):
                raise ValueError
        except ValueError:
            error = error or "PAYPAL_SANDBOX_RECEIVERS must be a JSON object of payee id to sandbox email."
            receivers = {}
    return PayPalConfig(mode=mode, client_id=cid, client_secret=secret, webhook_id=webhook,
                        receivers=receivers, timeout=timeout, error=error)
