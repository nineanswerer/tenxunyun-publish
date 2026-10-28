"""Look at the live page without clicking anything.

On 2026-10-27 the click was right and the server was asked to open the
order, but the run never recorded what the order panel looks like, so
nobody could tell a dialog it did not recognise from a dialog that never
opened. This tool answers that question and nothing else: it loads the
page, scrolls to the flash-sale module, and prints

  * every box on the page that mentions our price, with its class name,
  * what the finder would pick right now (usually: nothing).

It NEVER clicks. It never buys. It never logs in -- it uses the cookies
that are already in cookies.json.

    .venv\\Scripts\\python.exe check_dialog.py
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

from snap_browser import (BUY_MARKERS, CONFIRM_WORDS, DIALOG_FIND_JS,
                          DLG_TAG, PANEL_SEL, PRICE_RE, PRICE_SCAN_JS,
                          PRICE_TEXT)
from snap_config import ACTIVITY_URL, SECKILL_TITLE
from snap_target import GROUPS_JS, TARGET

sys.stdout.reconfigure(encoding="utf-8")

PAGE_URL = ACTIVITY_URL


def main():
    cookies = json.load(open("cookies.json", encoding="utf-8"))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        print("page:", page.url)

        try:
            groups = page.evaluate(GROUPS_JS, [TARGET, SECKILL_TITLE])
        except Exception as exc:
            groups = []
            print("module lookup failed:", exc)
        for g in groups:
            print("module: %-28s top=%s tabs=%s"
                  % ((g.get("title") or "")[:28], g.get("top"),
                     (g.get("tabs") or "")[:40]))

        boxes = []
        for g in groups:
            page.evaluate("window.scrollTo(0, %d)" % max(0, g["top"] - 250))
            page.wait_for_timeout(1500)
            try:
                here = page.evaluate(PRICE_SCAN_JS, [PRICE_TEXT]) or []
            except Exception:
                here = []
            if here:
                boxes = here
                break

        print()
        print("--- boxes that mention %s ---" % PRICE_TEXT)
        for b in boxes:
            print("  <%s class=%r> %dx%d buttons=%s"
                  % (b["tag"], b["cls"], b["w"], b["h"], b["buttons"]))
            print("      %s" % b["text"][:150])
        if not boxes:
            print("  (none -- the cards may not be on screen yet)")

        print()
        print("--- what the finder picks right now ---")
        found = page.evaluate(DIALOG_FIND_JS,
                              [PANEL_SEL, list(BUY_MARKERS), list(CONFIRM_WORDS),
                               DLG_TAG, PRICE_RE])
        if found:
            print("  it would pick: %dx%d  buttons=%s"
                  % (found["w"], found["h"], found["buttons"]))
            print("  text: %s" % found["text"][:200])
        else:
            print("  nothing -- which is right: no order panel is open")

        print()
        print("--- the buttons near our price ---")
        for b in boxes[:6]:
            for t in b["buttons"]:
                print("  %r" % t)

        browser.close()


if __name__ == "__main__":
    main()
