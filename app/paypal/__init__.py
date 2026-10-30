"""PayPal REST integration. Sandbox or simulator only; a live base URL does not exist in this code.

Who may import what (tests/test_architecture.py enforces it):
  reader.py     read-only calls behind a transport allowlist: the only part the agent's tools ever hold
  client.py     every money-moving call: imported by the execution gateway, reconcile, webhooks and scripts only
"""
