"""Read-only: does the module finder report the right section title?

The section title decides which module is the flash-sale one ("上云精选 ·
限时秒杀"). The card has its OWN title element (uno3-buy-card__title), and
that one must never be mistaken for the section title -- which is exactly
what the older lookup did.

Runs the lookup against:
  1. a LOCAL synthetic file that mirrors the real page structure
  2. the live page, anonymously (no cookies, no clicking)

Prints the module list only. It never clicks anything.

    .venv\\Scripts\\python.exe check_title.py
    .venv\\Scripts\\python.exe check_title.py --live
"""
import os
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

from snap_target import GROUPS_JS, TARGET

from snap_config import ACTIVITY_URL, PRICE_TEXT, SPEC_TEXT

PAGE_URL = ACTIVITY_URL
SECKILL_TITLE = "限时秒杀"

# the same shape as the live page: a section heading, then a module whose
# tab bar names PRODUCTS (not specs), whose cards carry their own title
# element, and whose first card is a skeleton with no button at all.
SYNTHETIC = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<div class="section">
  <h2>上云精选 · 限时秒杀</h2>
  <div class="qc-base-module uno3-buy-card-wrap">
    <div class="qc-base-grid__layout">
      <div class="qc-base-grid__row">
        <div class="uno3-buy-card uno3-buy-card-normal">
          <div class="uno3-buy-card__title">轻量应用服务器</div>
          <div class="uno3-buy-card__spec">__SPEC__3M</div>
          <div class="uno3-buy-card__price">活动价：__PRICE__</div>
          <div class="uno3-buy-card__btn">添加提醒</div>
        </div>
        <div class="uno3-buy-card uno3-skeleton">
          <div class="uno3-buy-card__title">轻量对象存储</div>
        </div>
      </div>
    </div>
  </div>
</div>
<div class="section">
  <h2>首单特惠</h2>
  <div class="qc-base-module uno3-buy-card-wrap">
    <div class="qc-base-grid__layout">
      <div class="qc-base-grid__row">
        <div class="uno3-buy-card uno3-buy-card-normal">
          <div class="uno3-buy-card__title">轻量应用服务器</div>
          <div class="uno3-buy-card__spec">__SPEC__3M</div>
          <div class="uno3-buy-card__price">活动价：109元</div>
          <div class="uno3-buy-card__btn">立即购买</div>
        </div>
      </div>
    </div>
  </div>
</div>
</body></html>"""


def show(groups):
    print("%d module(s)" % len(groups))
    for i, g in enumerate(groups):
        print("  [%d] seckill=%-5s spec=%-5s top=%-6d title=%-22r tabs=%s"
              % (i, g["isSeckill"], g["hasSpec"], g["top"], g["title"],
                 g["tabs"][:60]))
    seckill = [g for g in groups if g["isSeckill"]]
    if len(seckill) != 1:
        print("  FAIL: expected exactly one flash-sale module, got %d"
              % len(seckill))
        return False
    if not seckill[0]["title"].startswith("上云精选"):
        print("  FAIL: the flash-sale module's title is %r -- the card's own "
              "title element leaked in" % seckill[0]["title"])
        return False
    if groups[0] is not seckill[0]:
        print("  FAIL: the flash-sale module is not listed first")
        return False
    print("  ok: the flash-sale module is found, titled %r and listed first"
          % seckill[0]["title"])
    return True


def main():
    live = "--live" in sys.argv
    path = os.path.abspath("check_title_synthetic.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(SYNTHETIC)

    ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto("file:///" + path.replace("\\", "/"), wait_until="load")
        print("--- synthetic page ---")
        ok = show(page.evaluate(GROUPS_JS, [TARGET, SECKILL_TITLE])) and ok

        if live:
            print()
            print("--- live page (anonymous) ---")
            page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)
            groups = page.evaluate(GROUPS_JS, [TARGET, SECKILL_TITLE])
            print("%d module(s)" % len(groups))
            for i, g in enumerate(groups[:6]):
                print("  [%d] seckill=%-5s spec=%-5s top=%-6d title=%-22r tabs=%s"
                      % (i, g["isSeckill"], g["hasSpec"], g["top"], g["title"],
                         g["tabs"][:60]))
            hit = [g for g in groups if g["isSeckill"]]
            if len(hit) == 1:
                print("  ok: the live flash-sale module is %r" % hit[0]["title"])
            else:
                print("  FAIL: %d flash-sale modules live" % len(hit))
                ok = False
        browser.close()
    os.remove(path)
    raise SystemExit(0 if ok else 1)


# the sample page follows the configuration
SYNTHETIC = (SYNTHETIC.replace("__SPEC__", SPEC_TEXT)
                    .replace("__PRICE__", PRICE_TEXT))

if __name__ == "__main__":
    main()
