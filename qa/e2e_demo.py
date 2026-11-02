"""Run the whole keyless demo in a real browser and check what a person would see.

    python qa/e2e_demo.py                     starts its own keyless server on a free port, with a temporary data folder
    python qa/e2e_demo.py --base http://localhost:8000     uses a server you started
    python qa/e2e_demo.py --shots some/folder              where screenshots go (default qa/shots, not committed)
    python qa/e2e_demo.py --headed            watch it
    python qa/e2e_demo.py --video some/folder records silent draft footage (WebM)

Needs Playwright and Edge (channel="msedge"), or set E2E_CHANNEL (e.g. "chrome"), or E2E_CHANNEL=bundled. It never talks to
PayPal or to any model: PAYPAL_MODE=simulator, LLM_PROVIDER=offline. Exit code is nonzero if any check fails, and any CSP
violation or page error is a failure, which also catches AG Grid style hash drift after an upgrade.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
FAILS: list[str] = []
PROBLEMS: list[str] = []
LAST: list[Page] = []
expect.set_options(timeout=15000)


SILENT_SPEECH = "{ const s = speechSynthesis.speak.bind(speechSynthesis); speechSynthesis.speak = (u) => { u.volume = 0; s(u); }; }"


def check(name: str, ok: bool, got="") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{got}]" if got != "" else ""), flush=True)
    if not ok:
        FAILS.append(name)


def step(name: str, page: Page, fn) -> None:
    """A failed step is reported with a screenshot, and the run carries on with the next story."""
    print(f"\n== {name}", flush=True)
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        msg = " ".join(str(e).split())[:700]
        check(f"{name}: finished without error", False, msg)
        for i, target in enumerate(LAST or [page]):
            try:
                target.screenshot(path=str(SHOTS / f"FAILED-{re.sub(r'[^a-z0-9]+', '-', name.lower())}-{i}.png"))
            except Exception:  # noqa: BLE001
                pass
    LAST.clear()


def watch(page: Page, label: str) -> None:
    LAST.append(page)

    def on_console(m):
        if m.type == "error" and not re.search(r"Failed to load resource.*(400|403|404|409)|ERR_CONNECTION_RESET|ERR_FAILED", m.text):   # the lost-reply and no-network stories cut connections on purpose
            PROBLEMS.append(f"{label} console: {m.text[:600]}")

    page.on("console", on_console)
    page.on("pageerror", lambda e: PROBLEMS.append(f"{label} pageerror: {str(e)[:600]}"))


def shot(page: Page, name: str, full: bool = False) -> None:
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


def overflow(page: Page) -> int:
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def nav(page: Page, label: str) -> None:
    control = page.locator(".nav-btn", has_text=label)
    if not control.is_visible():                 # on a phone, the rest of the pages live under More
        page.locator("#nav-more").click()
    control.click()


def say(page: Page, text: str) -> None:
    if page.locator("#type-open").is_visible() and not page.locator("#text").is_visible():   # Care mode on a phone: typing opens on request
        page.locator("#type-open").click()
    page.locator("#text").fill(text)
    page.locator("#composer button[type=submit]").click()


def sign_card(page: Page) -> None:
    """Sign the open card: confirm, then the labelled simulated sensor."""
    page.locator("#c-go").click()
    page.locator(".mf-pad").click()


def last_tracker(page: Page):
    return page.locator(".tracker").last


def start_server(port: int, data: str, extra: dict | None = None):
    env = {**os.environ, "PAYPAL_MODE": "simulator", "LLM_PROVIDER": "offline", "ASR_PROVIDER": "browser", "PAYPAL_CLIENT_ID": "", "PAYPAL_CLIENT_SECRET": "", "PUBLIC_DEMO": "", "RULE_FIXED_HOUR": "14",   # no late-night rule depending on when this runs
           "LEDGER_PATH": os.path.join(data, "ledger.sqlite3"), "AUDIT_PATH": os.path.join(data, "audit.jsonl"),
           "PASSKEY_PATH": os.path.join(data, "passkeys.json"), "VOICE_DIR": os.path.join(data, "voice"), **(extra or {})}
    proc = subprocess.Popen([PY, "-m", "uvicorn", "app.server:app", "--port", str(port), "--log-level", "warning"], cwd=ROOT, env=env)
    for _ in range(80):
        try:
            if "paypal" in requests.get(f"http://localhost:{port}/api/health", timeout=1).json():   # this server, not another on that port
                return proc
        except (requests.RequestException, ValueError):
            time.sleep(0.25)
    proc.kill()
    raise SystemExit("The server didn't start.")


def main() -> int:
    global SHOTS
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    ap.add_argument("--shots", default=str(ROOT / "qa" / "shots"))
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--video")
    ap.add_argument("--only", help="run only the stories whose name contains this")
    args = ap.parse_args()
    SHOTS = Path(args.shots)
    SHOTS.mkdir(parents=True, exist_ok=True)
    proc = None
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    if args.base:
        base = args.base.rstrip("/")
    else:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        proc = start_server(port, tmp.name)
        base = f"http://localhost:{port}"
    try:
        with sync_playwright() as p:
            channel = os.environ.get("E2E_CHANNEL", "msedge")
            browser = p.chromium.launch(headless=not args.headed, **({} if channel == "bundled" else {"channel": channel}))
            new_context = browser.new_context
            def silent_context(**kw):  # the run shouldn't read aloud on the machine running it
                ctx = new_context(**kw)
                ctx.add_init_script(SILENT_SPEECH)
                return ctx
            browser.new_context = silent_context
            run(browser, base, args)
            browser.close()
    finally:
        if proc:
            proc.terminate()
            proc.wait(10)
        tmp.cleanup()
    for x in PROBLEMS:
        print("PROBLEM ", x)
    check("no CSP violations or page errors anywhere", not PROBLEMS, len(PROBLEMS))
    print(f"\n{'ALL PASSED' if not FAILS else 'FAILED: ' + '; '.join(FAILS)}")
    return 1 if FAILS else 0


def run(browser, base: str, args) -> None:
    kw = {"record_video_dir": args.video} if args.video else {}
    ctx = browser.new_context(viewport={"width": 1360, "height": 900}, accept_downloads=True, **kw)
    page = ctx.new_page()
    watch(page, "app")
    guardian_ctx = browser.new_context(viewport={"width": 1100, "height": 860})
    gp = guardian_ctx.new_page()
    watch(gp, "guardian")

    def landing():
        page.goto(base + "/")
        expect(page.locator("#welcome-title")).to_contain_text("She said $50")
        check("landing says it isn't affiliated", "not affiliated" in page.locator(".welcome-foot").inner_text())
        shot(page, "01-landing")
        page.locator("#welcome-enter").click()
        expect(page.locator(".hero h1")).to_contain_text("Mrs. Lee")
        expect(page.locator(".band-sim")).to_contain_text("SIMULATED")
        expect(page.locator("#last-pay")).to_contain_text("No payments yet")
        check("no horizontal overflow on Home", overflow(page) <= 0, overflow(page))
        check("a wide screen shows every page and no More button", not page.locator("#nav-more").is_visible() and page.locator(".nav-btn", has_text="Security lab").is_visible())
        shot(page, "02-home")
        for label, heading in (("Shop", "Shop"), ("Bills", "Bills"), ("Family", "Family"), ("Security lab", "Security lab"), ("Settings", "Settings")):
            nav(page, label)
            expect(page.locator(".page-head h1")).to_have_text(heading)
        nav(page, "Shop")
        expect(page.locator(".offer").first).to_be_visible()
        shot(page, "03-shop", True)
        check("catalogue shows a stop", page.locator(".offer.verdict-stop").count() >= 1)
        check("a listing's injected instruction is reported", page.locator(".flag-line").count() >= 1)
        nav(page, "Bills")
        expect(page.locator(".inv").first).to_be_visible()
        shot(page, "04-bills", True)
        nav(page, "Settings")
        shot(page, "05-settings", True)

    def send_money():
        nav(page, "Talk to Star")
        shot(page, "06-star-empty")
        page.locator(".choice", has_text="No, standard").click()
        expect(page.locator(".msg.bot").last).to_contain_text("standard view")
        say(page, "Send 50 to my daughter")
        expect(page.locator("#confirm .amt")).to_have_text("$50.00")
        expect(page.locator("#confirm .mode-tag").first).to_have_text("SIMULATED")
        shot(page, "07-card-send")
        page.locator("#c-go").click()
        expect(page.locator(".mockfp")).to_contain_text("simulated")
        shot(page, "08-fingerprint")
        page.locator(".mf-pad").click()
        expect(last_tracker(page)).to_be_visible()
        first = last_tracker(page).inner_text()
        check("signing alone is not reported as sent", "Sent, verified" not in first, first[:60])
        shot(page, "09-tracker-pending")
        expect(last_tracker(page)).to_contain_text("Sent, verified", timeout=30000)
        expect(last_tracker(page)).to_contain_text("Verified by")
        expect(page.locator(".counter b").first).to_have_text("1")
        expect(page.locator(".ticker").first).to_contain_text("POST /v1/payments/payouts")
        expect(page.locator(".msg.bot").last).to_contain_text("The simulator confirms")
        shot(page, "10-tracker-verified")

    def double_payment():
        say(page, "Send 50 to my daughter")
        expect(page.locator("#confirm .risk, .stop-block").first).to_be_visible()
        shot(page, "11-same-again")
        if page.locator("#confirm").evaluate("d => d.open"):
            page.locator("#c-cancel").click()
            expect(page.locator(".msg.bot").last).to_contain_text("cancelled")

    def purchase_and_refund():
        say(page, "Get me a blood pressure monitor")
        expect(page.locator("#confirm .offers")).to_contain_text("MedDeals")
        expect(page.locator("#confirm .offers")).to_contain_text("Stopped")
        shot(page, "12-card-purchase", True)
        sign_card(page)
        expect(page.locator(".tr-go")).to_be_visible()
        expect(last_tracker(page)).to_contain_text("Waiting for your approval")
        shot(page, "13-tracker-approve")
        page.locator(".tr-go").click()
        page.wait_for_url("**/simulator/checkout**")
        expect(page.locator(".sim-warning")).to_contain_text("THIS IS NOT PAYPAL")
        expect(page.locator(".sim-facts")).to_contain_text("$39.00")
        shot(page, "14-simulator-checkout")
        page.locator("#sim-yes").click()
        expect(last_tracker(page)).to_contain_text("Paid, verified", timeout=30000)
        check("query string is cleaned after the return", "pp_return" not in page.url, page.url)
        expect(page.locator(".msg.bot", has_text="simulator confirms").last).to_be_visible()
        expect(page.locator(".ticker").first).to_contain_text("/capture")
        shot(page, "15-paid-verified")
        page.locator(".tr-refund").click()
        expect(page.locator("#confirm h2")).to_contain_text("money back")
        shot(page, "16-card-refund")
        sign_card(page)
        expect(last_tracker(page)).to_contain_text("Refunded, verified", timeout=30000)
        shot(page, "17-refunded")

    def cancel_on_approval():
        say(page, "Order the weekly groceries box")
        expect(page.locator("#confirm .amt")).to_have_text("$62.00")
        sign_card(page)
        page.locator(".tr-go").click()
        page.wait_for_url("**/simulator/checkout**")
        page.locator("#sim-no").click()
        expect(last_tracker(page)).to_contain_text("You cancelled it", timeout=20000)
        expect(page.locator(".msg.bot", has_text="No money moved").last).to_be_visible()
        check("a cancel never reads as paid", "verified" not in last_tracker(page).inner_text().lower())
        shot(page, "18-buyer-cancelled")

    def pay_bill():
        nav(page, "Bills")
        page.locator(".inv", has_text="Riverside Power").locator(".btn.primary").click()
        expect(page.locator("#confirm .amt")).to_have_text("$86.40")
        expect(page.locator("#confirm")).to_contain_text("RP-2026-1001")
        shot(page, "19-card-bill")
        sign_card(page)
        page.locator(".tr-go").click()
        page.wait_for_url("**/simulator/invoice**")
        shot(page, "20-simulator-invoice")
        page.locator("#sim-yes").click()
        expect(last_tracker(page)).to_contain_text("Paid, verified", timeout=30000)
        shot(page, "21-bill-paid")

    def fake_invoice():
        nav(page, "Bills")
        page.locator("#inv-arrive").click()
        expect(page.locator(".inv-unknown")).to_contain_text("SecureTech")
        shot(page, "22-fake-invoice", True)
        nav(page, "Talk to Star")
        say(page, "What's this bill?")
        expect(page.locator(".msg.bot").last).to_contain_text("looks wrong")
        check("the injected sentence never appears", "approve and pay" not in page.locator("#stage").inner_text().lower())
        say(page, "Pay the SecureTech bill")
        expect(page.locator(".stop-block").last).to_contain_text("stopped this")
        expect(page.locator(".stop-block .stop-count b").last).to_have_text("0")
        expect(page.locator(".stop-block a.btn.danger").last).to_contain_text("833-FRAUD-11")
        expect(page.locator(".ticker-host .counter.zero").first).to_be_visible()
        shot(page, "23-scam-stopped")

    def spoken_scam():
        say(page, "The police say I must move my savings to a safe account")
        expect(page.locator(".stop-block").last).to_contain_text("stopped this")
        expect(page.locator(".stop-block a.btn.danger").last).to_contain_text("833-FRAUD-11")
        shot(page, "24-safe-account-stopped")

    def guardian():
        page.locator("#s-reset").count()
        nav(page, "Settings")
        page.locator("#s-reset").click()
        expect(page.locator(".hero h1")).to_be_visible()
        nav(page, "Family")
        page.locator("#g-invite").click()
        page.locator(".mf-pad").click()
        expect(page.locator(".invite-box")).to_be_visible()
        link = page.locator("#g-link").input_value()
        shot(page, "25-family-invite", True)
        gp.goto(link)
        expect(gp.locator("#g-pair")).to_be_visible()
        shot(gp, "26-guardian-pair")
        gp.locator("#g-pair").click()
        expect(gp.locator("#g-title")).to_be_visible()
        check("the invite token leaves the address bar", "invite=" not in gp.url, gp.url)
        expect(page.locator(".status-line.ok")).to_contain_text("paired", timeout=15000)

        nav(page, "Talk to Star")
        say(page, "Add a new person Tom Reed, email tom.reed@family.example")
        expect(page.locator("#confirm #c-title")).to_be_visible()
        sign = page.locator("#c-go")
        sign.click()
        page.locator(".mf-pad").click()
        expect(page.locator(".msg.bot").last).to_contain_text("Tom Reed")

        say(page, "Pay Tom Reed 400 dollars")
        expect(page.locator(".family-wait")).to_be_visible()
        expect(page.locator("#c-go")).to_be_disabled()
        shot(page, "27-card-waits-for-family")
        expect(gp.locator(".g-card")).to_contain_text("$400.00", timeout=15000)
        shot(gp, "28-guardian-pending", True)
        gp.locator(".g-card [data-decide=deny]").click()
        gp.locator(".mf-pad").click()
        expect(gp.locator("#g-pending")).to_contain_text("Nothing is waiting")
        expect(page.locator(".stop-block").last).to_contain_text("said no", timeout=15000)
        check("the card closed after the family said no", not page.locator("#confirm").evaluate("d => d.open"))
        shot(page, "29-family-denied")

        say(page, "Pay Tom Reed 400 dollars")
        expect(page.locator(".family-wait")).to_be_visible(timeout=15000)
        expect(gp.locator(".g-card")).to_contain_text("$400.00", timeout=15000)
        gp.locator(".g-card [data-decide=approve]").click()
        gp.locator(".mf-pad").click()
        expect(page.locator(".family-ok")).to_be_visible(timeout=15000)
        shot(page, "30-family-approved")
        if page.locator(".yesno").count():
            page.locator(".yesno button[data-v=yes]").click()
        expect(page.locator("#c-go")).to_be_enabled(timeout=20000)
        sign_card(page)
        expect(last_tracker(page)).to_contain_text("Sent, verified", timeout=30000)
        shot(page, "31-after-family")

        gp.locator("#g-freeze-on").click()
        expect(gp.locator("#g-freeze .status-line")).to_contain_text("frozen")
        nav(page, "Family")
        expect(page.locator(".frozen-card")).to_be_visible(timeout=10000)
        shot(page, "32-frozen", True)
        page.locator("#f-lift").click()
        page.locator(".mf-pad").click()
        expect(page.locator(".frozen-card")).to_contain_text("Waiting for Anna", timeout=10000)
        gp.locator("#g-lift").click()
        gp.locator(".mf-pad").click()
        expect(gp.locator("#g-freeze-on")).to_be_visible(timeout=10000)
        expect(gp.locator("#g-grid .ag-row").first).to_be_visible(timeout=15000)
        shot(gp, "33-guardian-grid", True)

    def lab():
        nav(page, "Security lab")
        page.locator("#lab-run").click()
        expect(page.locator(".lab-score").first).to_contain_text("attacks blocked", timeout=60000)
        score = page.locator(".lab-score").first.inner_text()
        m = re.match(r"(\d+) of (\d+)", score)
        check("every attack is blocked", bool(m) and m.group(1) == m.group(2), score)
        page.locator("#drill-run").click()
        expect(page.locator("#drill-out .lab-score")).to_contain_text("1 payout exists", timeout=60000)
        page.locator("#naive-run").click()
        expect(page.locator(".compare")).to_contain_text("It paid.", timeout=60000)
        check("comparison is labelled as scripted", "Scripted illustration" in page.locator("#naive-out").inner_text())
        check("Star's side made no money-moving call", "0 money-moving calls" in page.locator(".cmp.good").inner_text())
        page.locator("#contract-run").click()
        expect(page.locator("#contract-out h3").first).to_contain_text("behaved as expected", timeout=60000)
        shot(page, "34-lab", True)
        page.locator("#lab-mistake").click()
        expect(page.locator(".caught")).to_be_visible(timeout=20000)
        expect(page.locator(".caught-grid .cg.bad strong")).to_have_text("$500.00")
        expect(page.locator(".caught-grid .cg.good strong")).to_have_text("$50.00")
        shot(page, "35-model-mistake")

    def activity():
        nav(page, "Activity")
        grid = page.locator(".grid-host")
        rows = grid.locator(".ag-row:not(.row-total)")
        expect(rows.first).to_be_visible(timeout=20000)
        time.sleep(1)
        shot(page, "36-activity-grid", True)
        check("status always has its tag", "SIMULATED" in grid.inner_text())
        check("stops are in the grid", grid.locator(".row-stopped").count() >= 1, grid.locator(".row-stopped").count())
        check("totals are pinned at the bottom", grid.locator(".row-total", has_text="Total sent").count() == 1)
        check("stops are counted in a pinned row", grid.locator(".row-total", has_text="Stops in this demo").count() == 1)
        before = page.locator(".grid-count").inner_text()
        page.locator(".chip", has_text="Stopped").click()
        expect(page.locator(".chip", has_text="Stopped")).to_have_attribute("aria-pressed", "true")
        time.sleep(0.3)
        check("chip filters rows", page.locator(".grid-count").inner_text() != before, page.locator(".grid-count").inner_text())
        shot(page, "37-grid-stopped")
        page.locator(".chip", has_text="All").click()
        page.locator(".grid-search input").fill("Tom Reed")
        time.sleep(0.5)
        check("search filters rows", "Tom Reed" in grid.inner_text())
        page.locator(".grid-search input").fill("")
        check("the story of a stop reads without opening more columns", grid.locator(".row-stopped .grid-why").count() >= 1)
        grid.locator(".ag-header-cell", has_text="Amount").first.click()   # sort, then a real filter from the floating row, which opens on request
        time.sleep(0.3)
        page.locator("[data-act=more]").click()
        time.sleep(0.3)
        small = page.evaluate("[...document.querySelectorAll('.grid-host .ag-floating-filter-button-button')].filter(b => b.offsetParent).map(b => b.getBoundingClientRect().height).filter(h => h < 44)")
        check("column filter buttons are real targets", not small, small)
        grid.get_by_label("To / from Filter Input").fill("Tom")
        time.sleep(0.8)
        check("text column filter works", "Tom Reed" in grid.inner_text())
        shot(page, "38-grid-filter")
        grid.get_by_label("To / from Filter Input").fill("")
        time.sleep(0.5)
        grid.locator(".ag-floating-filter-button button").first.click()    # open a popup filter: more inline styles to test the CSP
        time.sleep(0.5)
        check("the filter popup opens", page.locator(".ag-filter-wrapper, .ag-filter").count() >= 1)
        page.locator(".page-head h1").click()                              # clicking elsewhere closes it
        time.sleep(0.3)
        with page.expect_download() as dl:
            page.locator("[data-act=csv]").click()
        path = dl.value.path()
        lines = list(csv.reader(open(path, encoding="utf-8")))
        check("CSV export has the rows and the totals", len(lines) > 3 and any("Total sent" in r for r in lines), len(lines))
        rows.filter(has_text="Money sent").first.click()
        expect(page.locator("#detail")).to_be_visible()
        expect(page.locator("#detail .timeline")).to_be_visible()
        check("timeline names a verified source", "Source" in page.locator("#detail .timeline").inner_text())
        shot(page, "39-row-detail")
        with page.expect_download() as dl:
            page.locator("#d-receipt").click()
        receipt = dl.value.path()
        out = subprocess.run([PY, str(ROOT / "scripts" / "verify_receipt.py"), str(receipt)], capture_output=True, text=True, cwd=ROOT)
        check("a downloaded receipt verifies offline", out.returncode == 0, (out.stdout + out.stderr).strip()[-500:])
        page.locator("#d-close").click()
        expect(page.locator("#integrity")).to_contain_text("Chain intact")
        cell = rows.first.locator(".ag-cell").first
        cell.click()                                 # a click opens the panel; Escape brings focus back to the cell
        expect(page.locator("#detail")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#detail")).to_be_hidden()
        page.keyboard.press("ArrowRight")
        focused = page.evaluate("document.activeElement?.className || ''")
        check("grid keyboard navigation moves focus", "ag-cell" in focused, focused[:50])

    def phone_and_care():
        phone = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        pp = phone.new_page()
        watch(pp, "phone")
        pp.goto(base + "/")
        shot(pp, "40-phone-landing")
        check("phone landing fits", overflow(pp) <= 0, overflow(pp))
        pp.locator("#welcome-enter").click()
        expect(pp.locator(".hero h1")).to_be_visible()
        for label in ("Home", "Talk to Star", "Shop", "Bills", "Family", "Activity", "Security lab", "Settings"):
            nav(pp, label)
            time.sleep(1.2 if label == "Activity" else 0.25)
            check(f"phone: {label} has no sideways scroll", overflow(pp) <= 0, overflow(pp))
            shot(pp, f"41-phone-{re.sub(r'[^a-z]+', '-', label.lower())}")
        # Wider font metrics exposed an intrinsic-width flex bug on Linux. Simulate them without hiding overflow or
        # changing the site's CSP: a rule in the existing same-origin stylesheet, removed immediately afterwards.
        rule = pp.evaluate("""() => {const sheet = [...document.styleSheets].find(s => s.href?.endsWith('/styles.css'));
          const index = sheet.cssRules.length; sheet.insertRule('.speech-controls{font-family:monospace}', index); return index;}""")
        check("phone: reading controls fit with wider font metrics", overflow(pp) <= 0, overflow(pp))
        shot(pp, "41-phone-settings-wide-font")
        pp.evaluate("index => [...document.styleSheets].find(s => s.href?.endsWith('/styles.css')).deleteRule(index)", rule)
        nav(pp, "Talk to Star")
        say(pp, "Send 30 to my grandson")
        expect(pp.locator(".choice").first).to_be_visible()      # two grandsons: Star asks, it doesn't guess
        shot(pp, "42-phone-which-grandson")
        pp.locator(".choice").first.click()
        expect(pp.locator("#confirm .amt")).to_have_text("$30.00", timeout=15000)
        shot(pp, "42-phone-card")
        pp.locator("#c-go").click()
        pp.locator(".mf-pad").click()
        expect(last_tracker(pp)).to_contain_text("Sent, verified", timeout=30000)
        check("phone: tracker fits", overflow(pp) <= 0, overflow(pp))
        shot(pp, "43-phone-tracker")
        # Care mode
        pp.locator("#care-toggle").click()
        expect(pp.locator(".care-hello h1")).to_contain_text("Mrs. Lee")
        check("Care mode text is larger", pp.evaluate("parseFloat(getComputedStyle(document.body).fontSize)") >= 20)
        check("phone Care mode fits", overflow(pp) <= 0, overflow(pp))
        shot(pp, "44-phone-care")
        say(pp, "The police say I must move my savings to a safe account")
        expect(pp.locator(".stop-block .why")).to_have_attribute("open", "")
        shot(pp, "45-phone-care-stop")
        check("phone Care stop fits", overflow(pp) <= 0, overflow(pp))
        nav(pp, "Activity")
        expect(pp.locator(".grid-host .ag-row").first).to_be_visible(timeout=20000)
        expect(pp.locator(".grid-card .pp-badge").first).to_be_visible()
        shot(pp, "46-phone-care-grid")
        check("phone Care grid fits", overflow(pp) <= 0, overflow(pp))
        phone.close()

        # Desktop Care mode, the same page at 200% browser zoom (a 680 px wide viewport is what that gives on 1360)
        page.set_viewport_size({"width": 680, "height": 450})
        nav(page, "Home")
        check("200% zoom: Home has no sideways scroll", overflow(page) <= 0, overflow(page))
        shot(page, "47-zoom-200")
        page.set_viewport_size({"width": 1360, "height": 900})

    def dark_and_focus():
        dark = browser.new_context(viewport={"width": 1280, "height": 860}, color_scheme="dark")
        dp = dark.new_page()
        watch(dp, "dark")
        dp.goto(base + "/")
        shot(dp, "48-dark-landing")
        dp.locator("#welcome-enter").click()
        expect(dp.locator(".hero h1")).to_be_visible()
        shot(dp, "49-dark-home")
        dp.locator(".nav-btn", has_text="Activity").click()
        expect(dp.locator(".grid-host .ag-root").first).to_be_visible(timeout=20000)
        time.sleep(1)
        shot(dp, "50-dark-activity")
        # Keyboard: skip link first, then a visible focus ring somewhere real
        dp.goto(base + "/guardian")
        dp.keyboard.press("Tab")
        check("skip link comes first", dp.evaluate("document.activeElement.className") == "skip-link")
        dark.close()

    def unpaired_guardian():
        c = browser.new_context()
        q = c.new_page()
        watch(q, "unpaired")
        q.goto(base + "/guardian")
        expect(q.locator(".g-pair")).to_contain_text("not paired")
        q.goto(base + "/guardian?invite=not-a-real-token")
        q.locator("#g-pair").click()
        expect(q.locator(".g-pair .alert-text")).to_contain_text("isn't valid")
        c.close()

    def lost_reply():
        # The signed card reaches the server and PayPal, then the browser never hears back. It must not offer to pay again.
        nav(page, "Talk to Star")
        writes = lambda: requests.get(f"{base}/api/paypal/calls", params={"session_id": page.evaluate("JSON.parse(localStorage.getItem('sid'))")}, timeout=5).json()["money_moving_total"]
        before = writes()

        def lose(route):
            route.fetch()
            route.abort("connectionreset")
        page.route("**/api/confirm", lose)
        trackers = page.locator(".tracker").count()
        say(page, "Send 25 to my son")
        expect(page.locator("#confirm .amt")).to_have_text("$25.00")
        sign_card(page)
        expect(page.locator(".tracker")).to_have_count(trackers + 1, timeout=20000)
        expect(last_tracker(page)).to_contain_text("Sent, verified", timeout=30000)
        page.unroute("**/api/confirm")
        check("a lost reply still made exactly one payout", writes() == before + 1, writes() - before)
        shot(page, "51-lost-reply")
        page.reload()
        expect(page.locator(".tracker").last).to_contain_text("Sent, verified")
        check("a reload keeps the conversation and skips the landing", page.locator("#welcome").is_hidden())
        check("a reload pays nothing", writes() == before + 1)

    def misconfigured():
        if args.base:
            return
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as data:
            server = start_server(port, data, {"PAYPAL_MODE": "sandbox", "SIGNING_SECRET": "e2e-only-not-a-real-secret"})
            c = browser.new_context(viewport={"width": 1280, "height": 860})
            q = c.new_page()
            watch(q, "sandbox-without-keys")
            try:
                q.goto(f"http://localhost:{port}/")
                q.locator("#welcome-enter").click()
                expect(q.locator(".band-bad")).to_contain_text("isn't set up")
                check("no SIMULATED success in a misconfigured sandbox", q.locator(".band-sim").count() == 0)
                shot(q, "52-sandbox-without-keys")
                q.locator(".nav-btn", has_text="Talk to Star").click()
                say(q, "Send 50 to my daughter")
                expect(q.locator(".stop-block").last).to_contain_text("can't reach PayPal")
                check("fails closed: no card and no tracker", q.locator(".tracker").count() == 0 and not q.locator("#confirm").evaluate("d => d.open"))
                check("an outage doesn't offer the scam hotline", q.locator(".stop-block a.btn.danger").count() == 0)
                shot(q, "53-sandbox-refused")
            finally:
                c.close()
                server.terminate()
                server.wait(10)

    def review_fixes():
        """What a person found with a phone in her hand and the scripted stories above didn't: layout under the navigation,
        the back button, a Freeze button that didn't freeze, a card signed with two Enter presses. Its own server: own state."""
        if args.base:
            return
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as data:
            server = start_server(port, data)
            url = f"http://localhost:{port}"
            c = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
            q = c.new_page()
            watch(q, "review-phone")
            try:
                q.goto(url + "/")
                check("the landing page doesn't overclaim", "Built on the PayPal sandbox" not in q.locator("body").inner_text()
                      and "stop scams and mistakes" not in q.locator("body").inner_text())
                q.locator("#welcome-enter").click()
                expect(q.locator(".hero h1")).to_be_visible()

                def rect(selector):
                    return q.locator(selector).first.bounding_box()
                # Five visible destinations, all on screen, in readable type
                visible = q.locator(".nav-btn:visible")
                boxes = [visible.nth(i).bounding_box() for i in range(visible.count())]
                check("phone navigation has five items", len(boxes) == 5, len(boxes))
                check("every navigation item is on screen", all(b["x"] >= 0 and b["x"] + b["width"] <= 390 for b in boxes), boxes)
                size = q.evaluate("parseFloat(getComputedStyle(document.querySelector('.nav-btn .nav-text')).fontSize)")
                check("navigation text is at least 13px", size >= 13, size)
                q.locator("#nav-more").click()
                check("More shows the rest", all(q.locator(".nav-btn", has_text=t).is_visible() for t in ("Shop", "Activity", "Settings", "Security lab")))
                check("the lab is marked as being for judges", "for judges" in q.locator("#nav-extra").inner_text().lower())
                q.locator(".nav-btn", has_text="Settings").click()
                expect(q.locator(".page-head h1")).to_have_text("Settings")
                check("More shows you are inside it", q.locator("#nav-more").get_attribute("aria-current") == "page")
                check("More closes after choosing", not q.locator(".nav-btn", has_text="Shop").is_visible())
                # The back button stays inside the app, and a reload stays on the page
                nav(q, "Home")
                nav(q, "Bills")
                nav(q, "Family")
                expect(q.locator(".page-head h1")).to_have_text("Family")
                q.go_back()
                expect(q.locator(".page-head h1")).to_have_text("Bills")
                check("back stayed in the app", q.url.startswith(url), q.url)
                q.reload()
                expect(q.locator(".page-head h1")).to_have_text("Bills")
                q.go_back()
                expect(q.locator(".hero h1")).to_be_visible()
                # Dates and copy
                nav(q, "Bills")
                expect(q.locator(".inv").first).to_be_visible()
                check("bill dates are in words", not re.search(r"\d{4}-\d{2}-\d{2}", q.locator("#inv-list").inner_text()), q.locator("#inv-list").inner_text()[:120])
                # A two-Enter payment: the card must make her look first
                nav(q, "Talk to Star")
                expect(q.locator(".choice", has_text="No, standard")).to_be_visible()
                q.locator(".choice", has_text="No, standard").click()
                expect(q.locator(".msg.bot").last).to_contain_text("standard view")
                placeholder = q.locator("#text").evaluate("t => t.scrollHeight <= t.clientHeight + 1")
                check("the input's hint fits", placeholder, q.locator("#text").get_attribute("placeholder"))
                say(q, "Send 15 to my son")
                expect(q.locator("#confirm .amt")).to_have_text("$15.00")
                time.sleep(0.2)
                check("the card doesn't open with focus on Confirm", q.evaluate("document.activeElement?.id") != "c-go", q.evaluate("document.activeElement?.id"))
                q.keyboard.press("Enter")
                q.keyboard.press("Enter")
                time.sleep(1.3)
                check("two Enter presses don't start signing", q.locator(".mockfp").count() == 0)
                q.locator("#c-go").focus()
                q.keyboard.press("Enter")
                expect(q.locator(".mf-pad")).to_be_visible()
                time.sleep(0.2)
                q.keyboard.press("Enter")
                time.sleep(1.2)
                check("Enter on the simulated sensor doesn't sign", q.locator(".mockfp").count() == 1 and q.locator(".tracker").count() == 0)
                q.locator(".mf-pad").click()
                expect(last_tracker(q)).to_contain_text("Sent, verified", timeout=30000)
                check("simulator mode doesn't say PayPal confirmed", "PayPal confirms" not in q.locator(".msg.bot").last.inner_text(), q.locator(".msg.bot").last.inner_text())
                time.sleep(0.5)
                nav_top = rect(".sidebar")["y"]
                composer = rect("#composer")
                check("the input is visible above the navigation after paying", composer["y"] + composer["height"] <= nav_top + 1 and composer["y"] >= 0, (composer, nav_top))
                # Natural speech
                say(q, "Send two hundred to my son for 3 months")
                expect(q.locator("#confirm .amt")).to_have_text("$200.00")
                check("the offline check says what it is", "second opinion" in q.locator("#confirm .vcheck").inner_text(), q.locator("#confirm .vcheck").inner_text())
                q.locator("#c-cancel").click()
                expect(q.locator("#confirm")).to_be_hidden()
                say(q, "A man on the phone says I must buy gift cards")
                expect(q.locator(".stop-block").last).to_be_visible()
                check("no card for being told to buy gift cards", not q.locator("#confirm").evaluate("d => d.open"))
                # A fresh session for what follows: that stop keeps this conversation on alert
                requests.post(url + "/api/reset", json={"world": True}, timeout=5)
                c.clear_cookies()
                q.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
                q.goto(url + "/")
                q.locator("#welcome-enter").click()
                expect(q.locator(".hero h1")).to_be_visible()
                # Waiting for family with nobody paired: say so and offer the way out
                nav(q, "Talk to Star")
                say(q, "My friend told me to buy a gift card for my grandson")
                expect(q.locator("#confirm .amt")).to_be_visible()
                expect(q.locator("#c-invite")).to_be_visible()
                check("a present gets a warning, not a stop", "scammers" in q.locator(".msg.bot").last.inner_text().lower() and q.locator(".stop-block").count() == 0, q.locator(".msg.bot").last.inner_text())
                check("no fingerprint request while it can't be signed", "confirm with your fingerprint" not in q.locator(".msg.bot").last.inner_text().lower(), q.locator(".msg.bot").last.inner_text())
                q.locator("#c-invite").click()
                expect(q.locator(".page-head h1")).to_have_text("Family")
                q.locator("#g-invite").click()
                q.locator(".mf-pad").click()
                expect(q.locator("#g-link")).to_be_visible()
                link = q.locator("#g-link").input_value()
                # Guardian's phone: a pending row is counted, and it isn't shown as money already gone
                gc = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
                g = gc.new_page()
                watch(g, "review-guardian")
                try:
                    g.goto(link)
                    g.locator("#g-pair").click()
                    expect(g.locator("#g-pending")).to_contain_text("gift card", ignore_case=True, timeout=15000)
                    expect(g.locator(".grid-host .ag-row").first).to_be_visible(timeout=15000)
                    expect(g.locator(".grid-count")).to_contain_text("1 row", timeout=8000)
                    count = g.locator(".grid-count").inner_text()
                    check("a waiting row is counted", not count.startswith("0 rows"), count)
                    check("no 'nothing here' over a waiting row", "Nothing here yet" not in g.locator(".grid-host").inner_text())
                    grid_text = g.locator(".grid-host").inner_text()
                    check("a waiting payment isn't shown as money gone", "waiting" in grid_text.lower() and "−$" not in grid_text, grid_text[:200])
                finally:
                    gc.close()
                # Activity: no summed "kept safe" number
                nav(q, "Activity")
                expect(q.locator(".grid-host .ag-root").first).to_be_visible(timeout=15000)
                time.sleep(0.5)
                grid_text = q.locator(".grid-host").inner_text()
                check("no 'kept safe' total", "kept safe" not in grid_text.lower(), grid_text[-200:])
                check("an empty ledger is one plain line, without zero totals", "Nothing here yet" in grid_text and q.locator(".grid-host .row-total").count() == 0, grid_text[-200:])
                grid_box = q.locator(".grid-host").bounding_box()
                check("an empty ledger stays short", grid_box["height"] < 240, grid_box["height"])
                # Freeze means freeze, one tap, from Home
                nav(q, "Home")
                q.locator("#home-freeze").click()
                expect(q.locator(".stat-bad")).to_contain_text("Frozen")
                check("Freeze now stays on Home", q.locator(".hero h1").is_visible())
                check("Freeze really froze", requests.get(url + "/api/state", params={"session_id": q.evaluate("JSON.parse(localStorage.getItem('sid'))")}, timeout=5).json()["frozen"]["on"])
                # Care mode: the voice button is on screen, above the navigation, and the offer is gone
                requests.post(url + "/api/reset", json={"world": True}, timeout=5)
                q.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
                q.goto(url + "/")
                q.locator("#welcome-enter").click()
                nav(q, "Talk to Star")
                expect(q.locator(".choice").first).to_be_visible()
                q.locator("#care-toggle").click()
                expect(q.locator(".care-hello h1")).to_be_visible()
                time.sleep(0.5)
                mic, bar = rect("#mic"), rect(".sidebar")
                check("Care: the voice button is on screen and above the navigation", mic["y"] >= 0 and mic["y"] + mic["height"] <= bar["y"] + 1 and q.locator("#mic").is_visible(), (mic, bar))
                check("Care: Star no longer offers the easier version", "easier version" not in q.locator("#stage").inner_text())
                check("Care: nothing overflows", overflow(q) <= 0, overflow(q))
                shot(q, "54-care-voice-button")
                # A wrong link
                q.goto(url + "/nope")
                check("a wrong link leads back to Star", q.locator("a", has_text="Back to Star").is_visible())
                check("the not-found page is sized for a phone", q.evaluate("document.documentElement.scrollWidth") <= 390)
            finally:
                c.close()
                server.terminate()
                server.wait(10)

    def narrow_and_failing():
        """What a design review found at 320 px, at 200% text and with the network gone. Its own server: own state."""
        if args.base:
            return
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as data:
            server = start_server(port, data)
            url = f"http://localhost:{port}"
            c = browser.new_context(viewport={"width": 320, "height": 640}, has_touch=True, is_mobile=True)
            q = c.new_page()
            watch(q, "narrow")
            try:
                def fits(label):
                    check(f"320: {label} has no sideways scroll", overflow(q) <= 0, overflow(q))
                # The first frame already carries the facts, and the phone's first screen ends in the zero
                q.goto(url + "/")
                state = q.evaluate("""() => { const z = getComputedStyle(document.querySelector('.plate .zero')), l = getComputedStyle(document.querySelector('.said-line'));
                  return [z.clipPath, z.opacity, l.opacity]; }""")
                check("the landing's facts are readable from the first frame", state == ["none", "1", "1"], state)
                zero, cta = q.locator(".plate .zero").bounding_box(), q.locator(".welcome-actions").bounding_box()
                check("320x640: the zero is on the first screen, above the buttons", zero["y"] + zero["height"] <= cta["y"], (zero, cta))
                check("320x640: SIMULATED is on the first screen", q.locator(".plate .zero .mode-tag").bounding_box()["y"] < cta["y"])
                fits("landing")
                q.locator("#welcome-enter").click()
                expect(q.locator(".hero h1")).to_be_visible()
                # Larger text from the browser
                q.evaluate("document.documentElement.style.fontSize = '200%'")
                time.sleep(0.3)
                fits("Home at 200% text")
                labels = q.evaluate("[...document.querySelectorAll('.nav-btn:not(.nav-extra .nav-btn) .nav-text')].filter(e => e.offsetParent).every(e => e.scrollWidth <= e.parentElement.clientWidth)")
                check("320: navigation labels fit at 200% text", labels)
                q.evaluate("document.documentElement.style.fontSize = ''")
                # The drill's long references wrap
                nav(q, "Security lab")
                expect(q.locator(".lab-score").first).to_contain_text("attacks blocked", timeout=60000)
                q.locator("#drill-run").click()
                expect(q.locator("#drill-out .lab-score")).to_contain_text("1 payout exists", timeout=60000)
                fits("failure drill result")
                # Care mode: the button she signs with, the dock, the stop
                nav(q, "Home")
                q.locator("#care-toggle").click()
                expect(q.locator(".care-hello h1")).to_be_visible()
                time.sleep(0.4)
                dock, bar = q.locator(".composer-wrap").bounding_box(), q.locator(".sidebar").bounding_box()
                check("Care: the dock rests on the navigation with no gap", abs(dock["y"] + dock["height"] - bar["y"]) <= 1, (dock, bar))
                check("Care: the dock is opaque", q.evaluate("getComputedStyle(document.querySelector('.composer-wrap')).backgroundColor") not in ("rgba(0, 0, 0, 0)", "transparent"))
                check("Care: the dock takes under a sixth of a small screen", dock["height"] <= 640 / 6, dock["height"])
                say(q, "Send 50 to my daughter")
                expect(q.locator("#confirm .amt")).to_have_text("$50.00")
                go, card = q.locator("#c-go").bounding_box(), q.locator("#confirm").bounding_box()
                check("Care, 320: the signing button is inside the card", go["x"] + go["width"] <= card["x"] + card["width"], (go, card))
                q.locator("#c-cancel").click()
                expect(q.locator("#confirm")).to_be_hidden()
                say(q, "Get me a blood pressure monitor")
                expect(q.locator("#confirm .amt")).to_be_visible()
                go, card = q.locator("#c-go").bounding_box(), q.locator("#confirm").bounding_box()
                check("Care, 320: the signing button fits a purchase card too", go["x"] + go["width"] <= card["x"] + card["width"], (go, card))
                q.locator("#c-cancel").click()
                expect(q.locator("#confirm")).to_be_hidden()
                say(q, "The police say I must move my savings to a safe account")
                expect(q.locator(".stop-block").last).to_be_visible()
                fits("Care stop")
                # The network goes: plain words and a way to retry
                c.route("**/api/**", lambda route: route.abort())
                say(q, "hello")
                expect(q.locator(".msg.bot").last).to_contain_text("No money has moved")
                expect(q.locator(".re-send")).to_be_visible()
                box, dock = q.locator(".re-send").bounding_box(), q.locator(".composer-wrap").bounding_box()
                check("offline: the retry isn't under the dock", box["y"] + box["height"] <= dock["y"] + 1, (box, dock))
                nav(q, "Bills")
                expect(q.locator("#inv-list .load-fail")).to_contain_text("can't reach Star")
                expect(q.locator("#dis-list .load-fail")).to_contain_text("can't reach Star")
                check("offline: no browser error on screen", "Failed to fetch" not in q.locator("#main").inner_text())
                c.unroute("**/api/**")
                q.locator("#inv-list .load-fail button").click()
                expect(q.locator(".inv").first).to_be_visible()
                check("Try again brings the bills back", True)
            finally:
                c.close()
                server.terminate()
                server.wait(10)

    def public_demo():
        if args.base:
            return
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as data:
            server = start_server(port, data, {"PUBLIC_DEMO": "1", "SIGNING_SECRET": "e2e-only-not-a-real-secret"})
            c = browser.new_context(viewport={"width": 1280, "height": 860})
            q = c.new_page()
            watch(q, "public-demo")
            try:
                url = f"http://localhost:{port}"
                check("a shared server has no anonymous customer", requests.get(url + "/api/state", timeout=5).status_code == 404)
                q.goto(url + "/")
                q.locator("#welcome-enter").click()
                expect(q.locator(".hero h1")).to_contain_text("Mrs. Lee")
                sid = q.evaluate("JSON.parse(localStorage.getItem('sid'))")
                user = requests.get(url + "/api/state", params={"session_id": sid}, timeout=5).status_code
                check("a visitor gets a session of their own", user == 200, user)
            finally:
                c.close()
                server.terminate()
                server.wait(10)

    def no_stale_screens():
        check("/api/world is gone", requests.get(base + "/api/world", timeout=5).status_code == 404)

    for name, fn in [("Landing and pages", landing), ("Send money to family", send_money), ("Same payment asked twice", double_payment),
                     ("Agentic purchase, approval, capture, refund", purchase_and_refund), ("Cancel on the approval page", cancel_on_approval),
                     ("Pay a bill by invoice", pay_bill), ("The fake invoice", fake_invoice), ("A spoken scam", spoken_scam),
                     ("Family guardian, two devices", guardian), ("Security lab and model mistake", lab), ("Activity grid", activity),
                     ("The reply gets lost", lost_reply), ("Sandbox without keys", misconfigured),
                     ("Phone widths and Care mode", phone_and_care), ("Dark theme and focus", dark_and_focus),
                     ("Guardian page without pairing", unpaired_guardian), ("Removed screens", no_stale_screens),
                     ("Review fixes on a phone", review_fixes), ("Narrow, large text, no network", narrow_and_failing), ("Public demo", public_demo)]:
        if not args.only or args.only.lower() in name.lower():
            step(name, page, fn)
    ctx.close()
    guardian_ctx.close()


if __name__ == "__main__":
    sys.exit(main())
