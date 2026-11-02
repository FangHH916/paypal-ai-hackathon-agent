"""Drive the keyless app through the demo in docs/DEMO_VIDEO.md, recording silent footage and screenshots.

    python scripts/capture_demo.py                     records to output/ (not committed)
    python scripts/capture_demo.py --publish           also copies the screenshots the README uses to docs/images
    python scripts/capture_demo.py --headed            watch it
    python scripts/capture_demo.py --base URL          uses a keyless server you started, instead of its own

Needs Playwright and Edge (channel="msedge"), or set E2E_CHANNEL (e.g. "chrome"), or E2E_CHANNEL=bundled. It starts a server in
simulator mode with the offline rule engine and a temporary data folder: it never talks to PayPal or to any model, so all it
can show is the SIMULATED path. Output:

    output/video/customer.webm    Mrs. Lee's screen, the whole demo, silent
    output/video/guardian.webm    Anna's screen, silent (cut it in during the family scene)
    output/shots/*.png            one screenshot per moment
    output/timeline.json          when each scene starts in customer.webm, in seconds
    output/receipt-check.txt      what scripts/verify_receipt.py said about the receipt it downloaded

Exit code is nonzero if a scene didn't finish, or if the page reported an error.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
SHOTS = OUT / "shots"
VIDEO = OUT / "video"
IMAGES = ROOT / "docs" / "images"
expect.set_options(timeout=30000)

# Screenshot name -> name in docs/images, for --publish. Keep in step with README.md.
PUBLISHED = {
    "01-landing": "landing.png",
    "02-care-home": "care-home.png",
    "05-purchase-card": "purchase-card.png",
    "06-simulator-checkout": "simulator-checkout.png",
    "07-paid-verified": "paid-verified.png",
    "10-fake-invoice-stopped": "fake-invoice-stopped.png",
    "11-naive-comparison": "naive-comparison.png",
    "13-card-waits-for-family": "card-waits-for-family.png",
    "14-guardian-pending": "guardian-pending.png",
    "16-model-mistake": "model-mistake.png",
    "17-failure-drill": "failure-drill.png",
    "18-activity-grid": "activity-grid.png",
    "19-grid-stopped": "activity-stopped.png",
}


SILENT_SPEECH = "{ const s = speechSynthesis.speak.bind(speechSynthesis); speechSynthesis.speak = (u) => { u.volume = 0; s(u); }; }"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int, data: str):
    env = {**os.environ, "PAYPAL_MODE": "simulator", "LLM_PROVIDER": "offline", "ASR_PROVIDER": "browser", "PAYPAL_CLIENT_ID": "", "PAYPAL_CLIENT_SECRET": "",
           "PUBLIC_DEMO": "", "RULE_FIXED_HOUR": "14",     # the footage shouldn't depend on the clock: no late-night rule at a late-night recording
           "LEDGER_PATH": os.path.join(data, "ledger.sqlite3"), "AUDIT_PATH": os.path.join(data, "audit.jsonl"),
           "PASSKEY_PATH": os.path.join(data, "passkeys.json"), "VOICE_DIR": os.path.join(data, "voice")}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--port", str(port), "--log-level", "warning"], cwd=ROOT, env=env)
    for _ in range(80):
        try:
            if "paypal" in requests.get(f"http://localhost:{port}/api/health", timeout=1).json():   # this server, not another on that port
                return proc
        except (requests.RequestException, ValueError):
            time.sleep(0.25)
    proc.kill()
    raise SystemExit("The server didn't start.")


class Run:
    def __init__(self, page: Page, guardian: Page, base: str):
        self.page, self.guardian, self.base = page, guardian, base
        self.t0 = time.monotonic()
        self.timeline: list[dict] = []
        self.problems: list[str] = []
        for p, label in ((page, "customer"), (guardian, "guardian")):
            p.on("pageerror", lambda e, label=label: self.problems.append(f"{label}: {str(e)[:300]}"))

    def scene(self, name: str) -> None:
        at = round(time.monotonic() - self.t0, 1)
        self.timeline.append({"scene": name, "at_seconds": at})
        print(f"{at:6.1f}s  {name}", flush=True)

    @staticmethod
    def shot(page: Page, name: str, full: bool = False) -> None:
        page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)

    def nav(self, label: str) -> None:
        self.page.locator(".nav-btn", has_text=label).click()
        time.sleep(0.8)

    def say(self, text: str, hold: float = 1.0) -> None:
        box = self.page.locator("#text")
        box.click()
        box.press_sequentially(text, delay=28)
        time.sleep(0.3)
        self.page.locator("#composer button[type=submit]").click()
        time.sleep(hold)

    def sign(self, page: Page | None = None) -> None:
        """Confirm the open card, then press the labelled simulated sensor."""
        p = page or self.page
        p.locator("#c-go").click()
        time.sleep(1.0)
        p.locator(".mf-pad").click()

    def tracker(self):
        return self.page.locator(".tracker").last


def demo(r: Run) -> None:
    page, gp = r.page, r.guardian

    r.scene("Hook: landing")
    page.goto(r.base + "/")
    expect(page.locator("#welcome-title")).to_contain_text("She said $50")
    time.sleep(0.5)
    r.shot(page, "01-landing")
    time.sleep(9)
    page.locator("#welcome-enter").click()
    expect(page.locator(".hero h1")).to_contain_text("Mrs. Lee")
    time.sleep(1.5)
    r.nav("Talk to Star")
    page.locator(".choice", has_text="Yes, make it easier").click()      # Star asks once; the same switch is in Settings
    expect(page.locator(".care-hello h1")).to_contain_text("Mrs. Lee")
    time.sleep(1.5)
    r.shot(page, "02-care-home")
    time.sleep(3)

    r.scene("Send money to family")
    r.say("Send 50 to my daughter")
    expect(page.locator("#confirm .amt")).to_have_text("$50.00")
    r.shot(page, "03-send-card")
    time.sleep(3)
    r.sign()
    expect(r.tracker()).to_contain_text("Sent, verified")
    expect(page.locator(".ticker").first).to_contain_text("POST /v1/payments/payouts")
    time.sleep(1)
    r.tracker().scroll_into_view_if_needed()
    r.shot(page, "04-send-verified")
    time.sleep(3)

    r.scene("Moment 1: an agent that really buys")
    r.say("Get me a blood pressure monitor")
    expect(page.locator("#confirm .offers")).to_contain_text("MedDeals")
    expect(page.locator("#confirm .offers")).to_contain_text("Stopped")
    time.sleep(3)
    page.locator("#confirm .offers").scroll_into_view_if_needed()     # the stopped offer is what this scene is about
    time.sleep(0.5)
    r.shot(page, "05-purchase-card")
    time.sleep(4)
    r.sign()
    expect(page.locator(".tr-go")).to_be_visible()
    time.sleep(2)
    page.locator(".tr-go").click()
    page.wait_for_url("**/simulator/checkout**")
    expect(page.locator(".sim-warning")).to_contain_text("THIS IS NOT PAYPAL")
    time.sleep(0.5)
    r.shot(page, "06-simulator-checkout")
    time.sleep(3.5)
    page.locator("#sim-yes").click()
    expect(r.tracker()).to_contain_text("Paid, verified")
    time.sleep(0.8)
    r.tracker().scroll_into_view_if_needed()
    r.shot(page, "07-paid-verified")
    time.sleep(3.5)
    page.locator(".tr-refund").click()
    expect(page.locator("#confirm h2")).to_contain_text("money back")
    r.shot(page, "08-refund-card")
    time.sleep(2)
    r.sign()
    expect(r.tracker()).to_contain_text("Refunded, verified")
    time.sleep(2.5)

    r.scene("Moment 2: the invoice that talked back")
    r.nav("Bills")
    page.locator("#inv-arrive").click()
    expect(page.locator(".inv-unknown")).to_contain_text("SecureTech")
    time.sleep(0.5)
    r.shot(page, "09-fake-invoice", True)
    time.sleep(3)
    r.nav("Home")                                   # in Care mode, Star lives on Home
    r.say("What's this bill?")
    expect(page.locator(".msg.bot").last).to_contain_text("looks wrong")
    time.sleep(3.5)
    r.say("Pay the SecureTech bill")
    expect(page.locator(".stop-block").last).to_contain_text("stopped this")
    expect(page.locator(".stop-block .stop-count b").last).to_have_text("0")
    page.locator(".stop-block").last.scroll_into_view_if_needed()
    time.sleep(0.5)
    r.shot(page, "10-fake-invoice-stopped")
    time.sleep(8)

    r.scene("Moment 3: the family guardian")
    r.nav("Family")
    page.locator("#g-invite").click()
    time.sleep(0.8)
    page.locator(".mf-pad").click()
    expect(page.locator(".invite-box")).to_be_visible()
    link = page.locator("#g-link").input_value()
    time.sleep(1.5)
    gp.goto(link)
    expect(gp.locator("#g-pair")).to_be_visible()
    gp.locator("#g-pair").click()
    expect(gp.locator("#g-title")).to_be_visible()
    r.shot(gp, "12-guardian-paired")
    expect(page.locator(".status-line.ok")).to_contain_text("paired")
    r.nav("Home")
    r.say("Add a new person Tom Reed, email tom.reed@family.example")
    expect(page.locator("#confirm #c-title")).to_be_visible()
    time.sleep(1.5)
    r.sign()
    expect(page.locator(".msg.bot").last).to_contain_text("Tom Reed")
    time.sleep(1)
    r.say("Pay Tom Reed 400 dollars")
    expect(page.locator(".family-wait")).to_be_visible()
    expect(page.locator("#c-go")).to_be_disabled()
    time.sleep(0.5)
    r.shot(page, "13-card-waits-for-family")
    time.sleep(3)
    expect(gp.locator(".g-card")).to_contain_text("$400.00")
    gp.bring_to_front()
    time.sleep(0.5)
    r.shot(gp, "14-guardian-pending", True)
    time.sleep(2.5)
    gp.locator(".g-card [data-decide=deny]").click()
    time.sleep(1.0)
    gp.locator(".mf-pad").click()
    expect(gp.locator("#g-pending")).to_contain_text("Nothing is waiting")
    page.bring_to_front()
    expect(page.locator(".stop-block").last).to_contain_text("said no")
    page.locator(".stop-block").last.scroll_into_view_if_needed()
    time.sleep(0.5)
    r.shot(page, "15-family-denied")
    time.sleep(3.5)

    r.scene("The lab: comparison, model mistake, failure drill")
    page.locator("#care-toggle").click()            # the lab is hidden in Care mode: it isn't for Mrs. Lee
    time.sleep(3)                                   # let the toast go before the screenshot
    r.nav("Security lab")
    page.locator("#naive-run").click()
    expect(page.locator(".compare")).to_contain_text("It paid.")
    page.locator(".compare").scroll_into_view_if_needed()
    time.sleep(0.5)
    r.shot(page, "11-naive-comparison")
    time.sleep(5)
    page.locator("#lab-mistake").click()
    expect(page.locator(".caught")).to_be_visible()
    expect(page.locator(".caught-grid .cg.bad strong")).to_have_text("$500.00")
    expect(page.locator(".caught-grid .cg.good strong")).to_have_text("$50.00")
    page.locator(".caught").scroll_into_view_if_needed()
    time.sleep(0.5)
    r.shot(page, "16-model-mistake")
    time.sleep(5)
    if page.locator("#confirm").evaluate("d => d.open"):       # the frozen card can't be signed: close it to get back to the lab
        page.locator("#c-cancel").click()
        time.sleep(0.5)
    r.nav("Security lab")
    page.locator("#drill-run").click()
    expect(page.locator("#drill-out .lab-score")).to_contain_text("1 payout exists")
    page.locator("#drill-out").scroll_into_view_if_needed()
    time.sleep(0.5)
    r.shot(page, "17-failure-drill")
    time.sleep(4)

    r.scene("Activity grid and audit chain")
    r.nav("Activity")
    grid = page.locator(".grid-host")
    rows = grid.locator(".ag-row:not(.row-total)")
    expect(rows.first).to_be_visible()
    time.sleep(1.5)
    r.shot(page, "18-activity-grid")
    time.sleep(2)
    page.locator(".chip", has_text="Stopped").click()
    expect(page.locator(".chip", has_text="Stopped")).to_have_attribute("aria-pressed", "true")
    time.sleep(1)
    r.shot(page, "19-grid-stopped")
    time.sleep(2.5)
    page.locator(".chip", has_text="All").click()
    time.sleep(0.5)
    rows.filter(has_text="Money sent").first.click()
    expect(page.locator("#detail .timeline")).to_be_visible()
    time.sleep(0.5)
    r.shot(page, "20-row-detail")
    time.sleep(3)
    with page.expect_download() as dl:
        page.locator("#d-receipt").click()
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_receipt.py"), str(dl.value.path())], capture_output=True, text=True, cwd=ROOT)
    (OUT / "receipt-check.txt").write_text(out.stdout + out.stderr, encoding="utf-8")
    if out.returncode:
        r.problems.append("the downloaded receipt didn't verify; see output/receipt-check.txt")
    page.locator("#d-close").click()
    expect(page.locator("#integrity")).to_contain_text("Chain intact")
    page.locator("#integrity").scroll_into_view_if_needed()
    time.sleep(2.5)

    r.scene("Close")
    r.nav("Home")
    time.sleep(1)
    r.shot(page, "21-close")
    time.sleep(9)
    r.scene("End")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="a keyless server you started; default is to start one")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--publish", action="store_true", help="copy the README's screenshots to docs/images")
    args = ap.parse_args()
    shutil.rmtree(VIDEO, ignore_errors=True)
    for d in (SHOTS, VIDEO):
        d.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    proc, base = None, (args.base or "").rstrip("/")
    if not base:
        port = free_port()
        proc = start_server(port, tmp.name)
        base = f"http://localhost:{port}"
    failed = ""
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
            size = {"width": 1280, "height": 800}
            ctx = browser.new_context(viewport=size, record_video_dir=str(VIDEO / "c"), record_video_size=size, accept_downloads=True)
            gctx = browser.new_context(viewport=size, record_video_dir=str(VIDEO / "g"), record_video_size=size)
            page, gp = ctx.new_page(), gctx.new_page()
            run = Run(page, gp, base)
            try:
                demo(run)
            except Exception as e:  # noqa: BLE001
                failed = " ".join(str(e).split())[:700]
                run.shot(page, "FAILED-customer")
                run.shot(gp, "FAILED-guardian")
            customer, guardian = page.video, gp.video
            ctx.close()
            gctx.close()
            browser.close()
            shutil.move(customer.path(), VIDEO / "customer.webm")
            shutil.move(guardian.path(), VIDEO / "guardian.webm")
    finally:
        if proc:
            proc.terminate()
            proc.wait(10)
        tmp.cleanup()
    shutil.rmtree(VIDEO / "c", ignore_errors=True)
    shutil.rmtree(VIDEO / "g", ignore_errors=True)
    (OUT / "timeline.json").write_text(json.dumps(run.timeline, indent=2) + "\n", encoding="utf-8")
    for x in run.problems:
        print("PROBLEM", x)
    if failed:
        print("FAILED:", failed)
        return 1
    if run.problems:
        return 1
    if args.publish:
        IMAGES.mkdir(parents=True, exist_ok=True)
        for src, dst in PUBLISHED.items():
            shutil.copyfile(SHOTS / f"{src}.png", IMAGES / dst)
        print(f"Copied {len(PUBLISHED)} screenshots to docs/images")
    print(f"Done. Footage length {run.timeline[-1]['at_seconds']} s. See {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
