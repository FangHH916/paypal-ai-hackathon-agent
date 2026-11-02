"""The rules the pages must keep, checked on the source. The real browser run is qa/e2e_demo.py."""
import json
import re
import unittest
from pathlib import Path

from app.server import STATIC
from app.wallet import payments

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PAGES = ("app.js", "common.js", "activity.js", "guardian.js", "simulator.js", "motion.js")


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

    def test_fonts_are_vendored_with_their_licence_and_nothing_loads_from_elsewhere(self):
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        licence = (WEB / "fonts" / "LICENSE.txt").read_text(encoding="utf-8")
        self.assertIn("Open Font License", licence)
        self.assertIn("web/fonts/", (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8"))
        self.assertNotRegex(css, r"url\(['\"]?https?:|@import")
        for url in re.findall(r"url\((/[^)]+)\)", css):
            self.assertIn(url, STATIC, url)
        self.assertLess(sum(p.stat().st_size for p in (WEB / "fonts").glob("*.woff2")), 100_000)

    def test_a_reader_who_asks_for_less_motion_gets_none_from_the_script(self):
        motion = (WEB / "motion.js").read_text(encoding="utf-8")
        self.assertIn("prefers-reduced-motion: reduce", motion)
        self.assertIn("prefers-reduced-motion:reduce", (WEB / "styles.css").read_text(encoding="utf-8"))

    def test_motion_never_hides_content_while_it_plays(self):
        """Emphasis only. The one fade left is the line work behind a stop, which carries no words."""
        motion = (WEB / "motion.js").read_text(encoding="utf-8")
        self.assertNotIn("clipPath", motion)
        self.assertEqual(re.findall(r"opacity:[^,}]+", motion), ["opacity:0", "opacity:.14"])
        self.assertIn("IntersectionObserver", motion)

    def test_a_failed_request_never_shows_the_browser_error(self):
        common = (WEB / "common.js").read_text(encoding="utf-8")
        self.assertIn("offline:true", common)
        self.assertNotIn("r.statusText", common)
        self.assertNotIn("No money", common[common.index("export const NO_LINK"):common.index("export function loadFailed")])   # that claim belongs to callers that know

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


    def test_main_text_reaches_seven_to_one(self):
        for palette in self.tokens():
            for bg in ("bg", "surface"):
                self.assertGreaterEqual(contrast(palette["ink"], palette[bg]), 7, bg)

    def test_the_stop_and_every_status_reach_seven_to_one_on_the_tint_they_are_printed_on(self):
        """The words that matter most sit on a tint, not on the page: check the pair as it is drawn, in every palette block."""
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        blocks = [dict(re.findall(r"--([\w-]+):(#[0-9a-f]{6})", b)) for b in re.findall(r"\{[^{}]*--bad:[^{}]*\}", css)]
        self.assertEqual(len(blocks), 5)     # light, dark by preference, dark by choice, ink plate, paper plate
        pairs = [("bad", "bad-bg"), ("bad", "bg"), ("bad", "surface"), ("accent", "bg"), ("accent", "surface"), ("accent-ink", "accent"), ("accent-ink", "bad"),
                 ("ink", "bad-bg"), ("ok", "ok-bg"), ("warn", "warn-bg"), ("pend", "pend-bg"), ("neutral", "neutral-bg")]
        for block in blocks:
            for fg, bg in pairs:
                self.assertGreaterEqual(contrast(block[fg], block[bg]), 7, f"{fg} on {bg}")


class TestPublishedClaims(unittest.TestCase):
    def test_the_security_page_counts_the_attacks_the_lab_really_runs(self):
        from app.lab import run_lab
        n = len(run_lab())
        self.assertIn(f"runs {n} fixed attacks", (ROOT / "docs" / "SECURITY.md").read_text(encoding="utf-8"))

    def test_nothing_says_built_on_the_sandbox_or_adds_up_money_kept_safe(self):
        for path in [*WEB.glob("*.html"), *[WEB / n for n in PAGES], ROOT / "README.md"]:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("Built on the PayPal sandbox", text, path.name)
            self.assertNotIn("kept safe", text.lower(), path.name)
        self.assertNotIn("Fixed rules stop scams", (WEB / "index.html").read_text(encoding="utf-8"))

    def test_only_a_live_second_model_is_called_independent_on_screen(self):
        words = (ROOT / "app" / "kernel" / "crosscheck.py").read_text(encoding="utf-8") + (ROOT / "app" / "kernel" / "executor.py").read_text(encoding="utf-8")
        self.assertNotIn("The independent check", words)
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertEqual([line for line in app.splitlines() if "independent check" in line.lower() and "live()" not in line], [])


class TestEvalGate(unittest.TestCase):
    def load(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("run_eval_gate", ROOT / "eval" / "run_eval.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_require_all_turns_a_scripted_failure_into_a_failed_run(self):
        code = self.load().exit_code
        self.assertEqual(code(True, 1.0, True), 0)
        self.assertEqual(code(True, 0.98, True), 1)
        self.assertEqual(code(True, 0.98, False), 0)
        self.assertEqual(code(False, 1.0, False), 1)

    def test_ci_uses_it(self):
        self.assertIn("run_eval.py --require-all", (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8"))


class TestSavedReceipt(unittest.TestCase):
    def test_a_receipt_saved_by_a_browser_still_hashes_the_same(self):
        """JavaScript writes 50.0 as 50. Found by the browser run: a saved receipt failed scripts/verify_receipt.py."""
        body = {"amount": 50.0, "nested": {"n": [1.0, 2.5]}, "text": "é"}
        body["receipt_sha256"] = payments.receipt_hash(body)
        as_browser = json.loads(json.dumps(body).replace("50.0", "50").replace("[1.0", "[1"))
        self.assertEqual(payments.receipt_hash(as_browser), body["receipt_sha256"])
        as_browser["nested"]["n"][1] = 3
        self.assertNotEqual(payments.receipt_hash(as_browser), body["receipt_sha256"])


class TestAuditWords(unittest.TestCase):
    """Seen in a rescore: the audit list mixed raw event names (paypal.webhook, paypal.call) with plain words. Every
    event the server can write has plain words on the page, and the page never prints a raw name."""

    def events(self) -> set[str]:
        found = set()
        for path in (ROOT / "app").rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            found.update(re.findall(r"(?:append|_log|sink\.append)\(\s*\"([a-z_]+\.[a-z_]+)\"", src))
            found.update(re.findall(r"\"event\":\s*\"([a-z_]+\.[a-z_]+)\"", src))
            for group in re.findall(r"for event in \(([^)]*)\)", src):
                found.update(re.findall(r"\"([a-z_]+\.[a-z_]+)\"", group))
        return found

    def test_every_event_has_plain_words(self):
        js = (WEB / "app.js").read_text(encoding="utf-8")
        table = re.search(r"const AUDIT_WORDS = \{(.*?)\};", js, re.S).group(1)
        words = {k: a or b for k, a, b in re.findall(r"""'([a-z_]+\.[a-z_]+)':\s*(?:'([^']+)'|"([^"]+)")""", table)}
        events = self.events()
        self.assertGreater(len(events), 30)
        self.assertIn("paypal.call", events)
        self.assertEqual(events - set(words), set())
        for name, text in words.items():
            self.assertNotRegex(text, r"^[a-z_]+\.[a-z_]+$", name)

    def test_the_list_never_prints_a_raw_event_name(self):
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertFalse("AUDIT_WORDS[e.event] || e.event" in js, "the raw name is the fallback")


class TestNoControlCharacters(unittest.TestCase):
    """A stray control character in a pattern (a backspace where \b was meant) silently stops it matching."""

    def test_source_files_hold_no_control_characters(self):
        bad = []
        for folder, pattern in (("web", "*.js"), ("web", "*.css"), ("web", "*.html"), ("app", "**/*.py")):
            for path in (ROOT / folder).glob(pattern):
                text = path.read_text(encoding="utf-8")
                if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
                    bad.append(str(path.relative_to(ROOT)))
        self.assertEqual(bad, [])


class TestPlainZero(unittest.TestCase):
    """Seen in a rescore: amounts and times in body text showed a slashed zero (the vendored Atkinson faces have no plain
    zero). Figures in body text and amounts come from a face with a plain zero; the slashed-zero mono is kept only for
    reference codes, where telling 0 from O matters."""
    CODES = {"code", ".mode-tag", ".ticker .code", "pre", ".invite-box input"}

    def setUp(self):
        self.css = re.sub(r"/\*.*?\*/", "", (WEB / "styles.css").read_text(encoding="utf-8"), flags=re.S)

    def test_body_figures_come_from_a_face_with_a_plain_zero(self):
        body = re.search(r"--body:([^;]+);", self.css).group(1).strip()
        self.assertTrue(body.startswith("'Plain Figures'"), body)
        faces = re.findall(r"@font-face\{font-family:'Plain Figures';[^}]*\}", self.css)
        self.assertGreaterEqual(len(faces), 2, "a regular and a bold face")
        for face in faces:
            self.assertIn("unicode-range:U+30-39", face)          # figures only: every letter stays Atkinson
            self.assertNotIn("atkinson", face.lower())
            self.assertNotRegex(face, r"url\(")                   # nothing new is fetched

    def test_mono_only_where_it_is_a_reference_code(self):
        users = set()
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*var\(--mono\)[^{}]*)\}", self.css):
            if selectors.strip().startswith(":root") or "--mono:" in body:
                continue
            users.update(s.strip() for s in selectors.split(","))
        self.assertLessEqual(users, self.CODES, users - self.CODES)


if __name__ == "__main__":
    unittest.main()
