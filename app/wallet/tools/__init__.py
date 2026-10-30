"""The agent's wallet tools, one module per area. Add a module, then list it here."""
from app.wallet.tools import allowance, commerce, invoices, limits, payees, refunds, transfers  # noqa: F401

MODULES = [allowance, payees, limits, transfers, commerce, invoices, refunds]


def all_tools():
    return [t for m in MODULES for t in m.TOOLS]
