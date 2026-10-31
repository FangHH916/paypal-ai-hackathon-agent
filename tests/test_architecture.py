"""The structure is the safety claim, so it is tested as structure. I1: the agent's code can't import the money-moving
client. I2: no tool reaches a money-moving call. I10: no live base URL exists anywhere."""
import ast
import re
import subprocess
import unittest
from pathlib import Path

from tests.paypal_helpers import World

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_FOR_AGENT = {"app.paypal.client", "app.paypal.simulator", "app.paypal.factory", "app.paypal.reconcile",
                       "app.paypal.webhooks", "app.wallet.gateway"}
MAY_IMPORT_CLIENT = {"app/paypal/factory.py", "app/paypal/reconcile.py", "app/paypal/client.py", "app/paypal/webhooks.py",
                     "app/wallet/gateway.py"}


def imports_of(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


class TestImports(unittest.TestCase):
    def agent_files(self):
        return list((ROOT / "app" / "agent").glob("*.py")) + list((ROOT / "app" / "wallet" / "tools").glob("*.py"))

    def test_i1_agent_code_never_imports_the_client_or_gateway(self):
        for f in self.agent_files():
            self.assertFalse(imports_of(f) & FORBIDDEN_FOR_AGENT, f)

    def test_only_named_modules_import_the_client(self):
        for f in (ROOT / "app").rglob("*.py"):
            rel = f.relative_to(ROOT).as_posix()
            if "app.paypal.client" in imports_of(f):
                self.assertIn(rel, MAY_IMPORT_CLIENT)

    def test_the_reader_does_not_import_the_client(self):
        self.assertNotIn("app.paypal.client", imports_of(ROOT / "app" / "paypal" / "reader.py"))

    def test_kernel_trust_and_rules_do_not_touch_paypal_at_all(self):
        for name in ("trust", "rules", "crosscheck", "evidence", "contract"):
            self.assertFalse([i for i in imports_of(ROOT / "app" / "kernel" / f"{name}.py") if i.startswith("app.paypal")], name)


class TestTools(World):
    MONEY = re.compile(r"capture|execute|commit|pay_order|pay_invoice|send_money|create_payout|create_order|refund_capture|cancel_payout|create_invoice|send_invoice|authorize")

    def test_i2_no_tool_name_moves_money(self):
        for name in self.tools.tools:
            self.assertFalse(self.MONEY.search(name), name)

    def test_i2_every_tool_is_read_draft_or_control(self):
        self.assertEqual({t.tier for t in self.tools.tools.values()}, {"read", "draft", "control"})

    def test_i2_no_tool_call_writes_to_paypal(self):
        """Call every tool with plausible arguments: none may reach a money-moving request."""
        args = {"draft_transfer": {"payee_id": "p_daughter", "amount": 5}, "draft_purchase": {"offer_id": "of_bp_corner"},
                "draft_bill_payment": {"invoice_id": "inv_power_oct"}, "draft_money_request": {"payee_id": "p_son", "amount": 5},
                "search_catalogue": {"query": "blood pressure monitor"}, "explain_invoice": {"invoice_id": "inv_power_oct"},
                "request_add_payee": {"name": "Tom Reed", "email": "tom.reed@mail.example"}, "set_daily_limit": {"amount": 10},
                "queue_follow_up": {"task": "send 5 to my son"}, "set_care_mode": {"on": True}, "handoff_to_human": {"reason": "x"},
                "draft_refund": {"payment_id": "x"}, "draft_stop_payout": {"payment_id": "x"}}
        self.s.messages.append({"role": "user", "content": "send 5 to my son, freeze, tom.reed@mail.example Tom Reed, limit 10"})
        for name, tool in self.tools.tools.items():
            self.s.settle()
            self.s.current_draft_id = None
            from app.models import Phase
            self.s.phase = Phase.IDLE
            self.tools.call(self.s, name, args.get(name, {}))
        self.assertEqual(self.writes(), [], [w["path"] for w in self.writes()])
        self.assertEqual(self.gateway.calls.money_moving_count("u_lee"), 0)

    def test_the_reader_given_to_tools_cannot_write(self):
        from app.paypal.errors import ReadOnlyViolation
        with self.assertRaises(ReadOnlyViolation):
            self.gateway.reader.t.request("x", "POST", "/v1/payments/payouts", body={})

    def test_draft_purchase_has_no_price_argument(self):
        props = self.tools.tools["draft_purchase"].args.model_json_schema()["properties"]
        self.assertEqual(set(props), {"offer_id", "quantity", "evidence"})
        self.assertNotIn("amount", self.tools.tools["draft_bill_payment"].args.model_json_schema()["properties"])
        self.assertNotIn("amount", self.tools.tools["draft_refund"].args.model_json_schema()["properties"])


class TestNoLiveUrl(unittest.TestCase):
    def test_i10_no_live_base_url_in_the_tree(self):
        files = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, text=True).split("\n")
        needle = "api-m." + "paypal.com"                # split so this file doesn't match itself
        for name in files:
            path = ROOT / name
            if not name or not path.is_file() or path.suffix in (".png", ".jpg", ".pdf", ".wav") or "vendor" in name or name == "docs/PLAN.md":
                continue
            try:
                self.assertNotIn(needle, path.read_text(encoding="utf-8"), name)
            except UnicodeDecodeError:
                continue
