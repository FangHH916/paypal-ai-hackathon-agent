"""Build the PayPal objects for one mode. The only place that decides between simulator and sandbox.

There is no fallback: sandbox with missing keys gives clients whose every call raises PayPalConfigError, and the rules
answer R09_RAIL_UNAVAILABLE. Nothing ever quietly becomes the simulator.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.paypal import calls
from app.paypal.client import PayPalClient
from app.paypal.config import PayPalConfig
from app.paypal.reader import PayPalReader, read_only_transport
from app.paypal.simulator import Simulator, SimulatorAdapter
from app.paypal.transport import PayPalTransport


@dataclass
class PayPal:
    cfg: PayPalConfig
    client: PayPalClient
    reader: PayPalReader
    simulator: Simulator | None
    log: calls.CallLog


def build(cfg: PayPalConfig | None = None, *, audit=None, store_path=None, settle_seconds: float = 0.0,
          webhook_delay: float = 0.0) -> PayPal:
    cfg = cfg or PayPalConfig()
    log = calls.CallLog(audit)
    sim = Simulator(store_path, settle_seconds=settle_seconds, webhook_delay=webhook_delay) if cfg.mode == "simulator" and cfg.configured else None
    adapter = SimulatorAdapter(sim) if sim else None
    client = PayPalClient(PayPalTransport(cfg, adapter=adapter, log=log))
    reader = PayPalReader(read_only_transport(cfg, adapter=adapter, log=log))
    return PayPal(cfg, client, reader, sim, log)
