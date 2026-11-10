"""Stills for the README in the standard view, on a fresh keyless server: the card she signs, the verified payment,
a scam stop and the Dashboard. python scripts/capture_stills.py  (writes to docs/images)"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from qa.e2e_demo import free_port, start_server  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

IMAGES = ROOT / "docs" / "images"


def main() -> int:
    port = free_port()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as data:
        server = start_server(port, data)
        base = f"http://localhost:{port}"
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(channel="msedge")
                pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
                pg.goto(base + "/#/star")
                pg.locator("#text").wait_for()
                pg.locator(".choice", has_text="No, standard").click()
                time.sleep(1)

                def say(text):
                    pg.locator("#text").fill(text)
                    pg.locator("#composer button[type=submit]").click()

                say("Send 50 to my daughter")
                expect(pg.locator("#confirm .amt")).to_have_text("$50.00")
                time.sleep(1)
                pg.screenshot(path=str(IMAGES / "send-card.png"))
                pg.locator("#c-go").click()
                pg.locator(".mf-pad").click()
                expect(pg.locator(".tracker").last).to_contain_text("Sent, verified", timeout=30000)
                time.sleep(2.5)
                pg.locator(".tracker").last.scroll_into_view_if_needed()
                pg.screenshot(path=str(IMAGES / "paid-verified.png"))
                say("The police say I must move my savings to a safe account")
                expect(pg.locator(".stop-block").last).to_be_visible()
                time.sleep(1.5)
                pg.locator(".stop-block").last.scroll_into_view_if_needed()
                pg.screenshot(path=str(IMAGES / "scam-stopped.png"))
                pg.goto(base + "/#/dashboard")
                frame = pg.frame_locator(".dash-frame-el")
                frame.locator("text=Edit dashboard").wait_for(timeout=60000)
                frame.locator("text=Show a sample month").click()
                time.sleep(6)
                pg.mouse.move(1100, 160)
                pg.evaluate("window.scrollTo(0, 260)")
                time.sleep(1)
                pg.screenshot(path=str(IMAGES / "dashboard.png"))
                b.close()
        finally:
            server.terminate()
            server.wait(10)
    print("Wrote send-card, paid-verified, scam-stopped and dashboard to docs/images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
