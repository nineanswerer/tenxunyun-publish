"""Read-only: does the locator find the 38元 4核4G card on the live page?

Runs the very same JS the buyer uses (RESOLVE_JS / GROUPS_JS from
snap_target.py) against the live page and prints the candidates it comes
back with. Nothing is clicked, nothing is bought.

The cards are skeletons until their module is on screen, so every module is
scrolled into view in turn -- exactly like the buy script does.

    .venv\\Scripts\\python.exe check_target.py
    .venv\\Scripts\\python.exe check_target.py --cookies
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

from snap_target import (BTN_CLASS, CARD_ATTR, GROUPS_JS, IDX_ATTR, MARKER,
                         RESOLVE_JS, SEKILL_WORDS, TARGET, pick, score)

from snap_config import ACTIVITY_URL, MARKER_TEXT, PRICE_TEXT, SPEC_TEXT

PAGE_URL = ACTIVITY_URL
SECKILL_TITLE = "限时秒杀"


def main():
    use_cookies = "--cookies" in sys.argv
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        if use_cookies:
            context.add_cookies(json.load(open("cookies.json", encoding="utf-8")))
        page = context.new_page()
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        groups = page.evaluate(GROUPS_JS, [TARGET, SECKILL_TITLE])
        print("%d module(s); cookies=%s\n" % (len(groups), use_cookies))

        allc = []
        seen = set()
        for g in groups:
            page.evaluate("window.scrollTo(0, %d)" % max(0, g["top"] - 250))
            page.wait_for_timeout(1400)
            try:
                cands = page.evaluate(
                    RESOLVE_JS,
                    [TARGET, MARKER, BTN_CLASS, list(SEKILL_WORDS),
                     IDX_ATTR, CARD_ATTR])
            except Exception as exc:
                print("  module %r: scan failed: %s" % (g["title"], str(exc)[:120]))
                continue
            # the scan returns EVERY button on the page, not just this
            # module's, so the same card comes back once per module
            for c in cands:
                if not (c.get("hasTarget") or c.get("byTab")):
                    continue
                key = (c["text"], c["card"][:80], c.get("title") or "")
                if key in seen:
                    continue
                seen.add(key)
                allc.append(c)

        browser.close()

    print("%d distinct card(s) mention %s:\n" % (len(allc), SPEC_TEXT))
    for c in allc:
        print("  btn=%-8r score=%-2d %s=%-5s %s=%-5s byTab=%-5s top=%-6d [%s]"
              % (c["text"], score(c), PRICE_TEXT, c.get("has38"),
                 MARKER_TEXT, c.get("hasMarker"),
                 c.get("byTab"), c.get("top"), (c.get("title") or "")[:20]))
        print("        %s" % c["card"][:110])

    best = pick(allc)
    print()
    if best:
        print("==> the buyer would target: btn=%r  %s=%s  section=%r"
              % (best["text"], PRICE_TEXT, best.get("has38"),
                 best.get("title")))
    else:
        print("==> the buyer would find NOTHING -- this is the failure to fix")


if __name__ == "__main__":
    main()
