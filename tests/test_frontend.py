"""The rules the pages must keep, checked on the source. The real browser run is qa/e2e_demo.py."""
import json
import re
import unittest
from pathlib import Path

from app.server import STATIC
from app.wallet import payments

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PAGES = ("app.js", "common.js", "activity.js", "guardian.js", "simulator.js")


def luminance(hex_color: str) -> float:
    rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class TestStaticFiles(unittest.TestCase):
    def test_everything_allowed_exists(self):
        for route, (name, _) in STATIC.items():
            self.assertTrue((WEB / name).is_file(), route)

    def test_grid_is_vendored_with_its_licence(self):
        self.assertIn("MIT", (WEB / "vendor" / "ag-grid" / "LICENSE.txt").read_text(encoding="utf-8"))

    def test_no_third_party_code_or_inline_code(self):
        for path in WEB.glob("*.html"):
            html = path.read_text(encoding="utf-8")
            self.assertNotRegex(html, r"<style|\sstyle=|\son\w+=", path.name)
            self.assertNotRegex(html, r"<script(?![^>]*src=\"/)", path.name)
            self.assertNotRegex(html, r"(src|href)=\"https?://", path.name)
        for name in PAGES:
            self.assertNotRegex((WEB / name).read_text(encoding="utf-8"), r"\sstyle=\"|https?://(?!localhost)", name)

    def test_grid_pages_carry_the_style_nonce_and_the_simulator_does_not_need_one(self):
        self.assertIn("__STYLE_NONCE__", (WEB / "index.html").read_text(encoding="utf-8"))
        self.assertIn("__STYLE_NONCE__", (WEB / "guardian.html").read_text(encoding="utf-8"))
        self.assertNotIn("__STYLE_NONCE__", (WEB / "simulator.html").read_text(encoding="utf-8"))


class TestStatusLabelling(unittest.TestCase):
    def test_success_words_only_exist_in_the_one_helper(self):
        """'Paid, verified' and friends are built by statusInfo, which needs verified state. No page writes them itself."""
        for name in PAGES:
            source = (WEB / name).read_text(encoding="utf-8")
            hits = re.findall(r"[>'\"`](?:Paid|Sent|Refunded)(?:, verified)?[<'\"`]", source)
            if name == "common.js":
                self.assertTrue(hits)
            else:
                self.assertEqual(hits, [], name)

    def test_the_badge_always_carries_the_mode_tag(self):
        common = (WEB / "common.js").read_text(encoding="utf-8")
        body = common[common.index("export function ppBadge"):]
        self.assertIn("modeTag(label)", body[:body.index("\n}")])

    def test_every_payment_state_has_words(self):
        common = (WEB / "common.js").read_text(encoding="utf-8")
        for state in set(payments.ALLOWED) | payments.TERMINAL | {"STOPPED", "FROZEN", "DENIED"}:
            self.assertRegex(common, rf"\b{state}:", state)

    def test_disclaimer_and_simulator_label_are_on_the_pages(self):
        for name in ("index.html", "guardian.html"):
            self.assertIn("not affiliated with PayPal", (WEB / name).read_text(encoding="utf-8"), name)
        self.assertIn("THIS IS NOT PAYPAL", (WEB / "simulator.html").read_text(encoding="utf-8"))


class TestContrast(unittest.TestCase):
    """WCAG AA for text on its own background, in both palettes."""

    def tokens(self):
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        light = dict(re.findall(r"--([\w-]+):(#[0-9a-f]{6})", css[css.index(":root{"):css.index("@media (prefers-color-scheme:dark)")]))
        dark = dict(re.findall(r"--([\w-]+):(#[0-9a-f]{6})", css[css.index(":root[data-theme=\"dark\"]"):css.index("*{box-sizing")]))
        return light, dark

    def test_pairs(self):
        pairs = [("ink", "bg"), ("ink", "surface"), ("ink", "surface-2"), ("muted", "bg"), ("muted", "surface"), ("muted", "surface-2"), ("brand-ink", "brand"),
                 ("brand", "surface"), ("accent-ink", "accent"), ("ok", "ok-bg"), ("warn", "warn-bg"), ("bad", "bad-bg"), ("pend", "pend-bg"), ("neutral", "neutral-bg"),
                 ("ink", "bad-bg"), ("ink", "warn-bg"), ("ink", "ok-bg"), ("muted", "bad-bg"), ("muted", "pend-bg")]
        for palette in self.tokens():
            for fg, bg in pairs:
                self.assertGreaterEqual(contrast(palette[fg], palette[bg]), 4.5, f"{fg} on {bg}")


class TestSavedReceipt(unittest.TestCase):
    def test_a_receipt_saved_by_a_browser_still_hashes_the_same(self):
        """JavaScript writes 50.0 as 50. Found by the browser run: a saved receipt failed scripts/verify_receipt.py."""
        body = {"amount": 50.0, "nested": {"n": [1.0, 2.5]}, "text": "é"}
        body["receipt_sha256"] = payments.receipt_hash(body)
        as_browser = json.loads(json.dumps(body).replace("50.0", "50").replace("[1.0", "[1"))
        self.assertEqual(payments.receipt_hash(as_browser), body["receipt_sha256"])
        as_browser["nested"]["n"][1] = 3
        self.assertNotEqual(payments.receipt_hash(as_browser), body["receipt_sha256"])


if __name__ == "__main__":
    unittest.main()
