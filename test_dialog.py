"""Offline test of the buy-dialog handling.

The dialog is the one part of the buyer that is hard to see for real: a
stray 确定 somewhere else on the page once closed the dialog before the
order was ever created. So the rules are pinned down here against a
synthetic page that carries every trap:

  * a full-screen mask that contains the product card (it names our product
    and is huge -- it must NOT be taken for the dialog),
  * a promo banner that names our product but has no confirm button,
  * a stray 确定 in the footer of the page,
  * a second, cheaper product's panel with its own 确认,
  * the real dialog, whose class name is NOT one we know.

Checks: the dialog is found by its CONTENT, the region is taken from inside
that same dialog, the confirm button is the dialog's own 确认购买 (and never
the stray 确定), and when the dialog's class is unknown the fallback only
clicks a word that is unique on the page.

The fixture follows config.json, so it always describes the product the
buyer is currently aimed at.

No network, no account, no purchase: the page is a local string.

    .venv\\Scripts\\python.exe test_dialog.py
"""
import os
import sys

from playwright.sync_api import sync_playwright

import snap_config
from snap_browser import (CONFIRM_WORDS, DIALOG_CONFIRM, DIALOG_CONFIRM_JS,
                          DIALOG_FIND_JS, DIALOG_REGION_JS, DLG_TAG, PANEL_SEL,
                          STRONG_CONFIRM)

sys.stdout.reconfigure(encoding="utf-8")

SPEC = snap_config.SPEC
MARKER = snap_config.MARKER
PRICE = snap_config.PRICE
PRICE_TAG = "%s元" % PRICE
OTHER_PRICE = str(int(PRICE) + 61)
REGIONS = list(snap_config.REGIONS)
MARKERS = snap_config.dialog_markers()

# The dialog, drawn like the real one: the product line, the price, the
# region, and a footer with a cancel and a confirm button.
DIALOG = """
<div class="qc-modal buy-dialog" style="position:fixed;left:200px;top:120px;
     width:520px;background:#fff;padding:16px;z-index:10">
  <div class="buy-dialog__title">确认订单</div>
  <div class="buy-dialog__body">
    <div class="buy-dialog__goods">轻量应用服务器 %(spec)s %(marker)s 月流量</div>
    <div class="buy-dialog__price">活动价：%(price)s元</div>
    <div class="buy-dialog__region">地域：
      <span class="region-item">%(r0)s</span>
      <span class="region-item">%(r1)s</span>
      <span class="region-item">%(r2)s</span>
    </div>
  </div>
  <div class="buy-dialog__footer">
    <button class="btn btn-cancel">取消</button>
    <button class="btn btn-ok">确认购买</button>
  </div>
</div>
"""

# The same dialog, but with a class name nobody would guess.
DIALOG_ODD = DIALOG.replace("qc-modal buy-dialog", "xyz-order-box")

# A full-screen overlay that happens to contain the product card: it names our
# product and it is huge, so it must never be taken for the dialog.
MASK = """
<div class="activity-mask" style="position:fixed;inset:0;background:rgba(0,0,0,.4);
     z-index:5">
  <div class="uno3-buy-card uno3-buy-card-normal">
    <div class="uno3-buy-card__spec">%(spec)s %(marker)s 月流量</div>
    <div class="uno3-buy-card__price">活动价：%(price)s元</div>
    <div class="uno3-buy-card__btn">立即抢购</div>
  </div>
</div>
"""

# A promo banner that names our product but carries no confirm button.
BANNER = """
<div class="promo-panel" style="margin:40px">
  <div>%(spec)s %(price)s元 限量特惠</div>
  <a href="#">了解更多</a>
</div>
"""

# Another product's panel, with its own confirm button.
DECOY = """
<div class="other-modal" style="margin:20px;width:300px">
  <div>轻量对象存储 企业网盘 %(other)s元</div>
  <button>确定</button>
</div>
"""

FOOTER = """
<div class="page-footer" style="margin:40px">
  <button>确定</button>
</div>
"""

PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<script>
window.__clicked = [];
document.addEventListener("click", function (e) {
    var t = (e.target.innerText || e.target.textContent || "").trim();
    window.__clicked.push(t);
}, true);
</script>
%s
%s
%s
%s
%s
</body></html>"""

FILL = {"spec": SPEC, "marker": MARKER, "price": PRICE, "other": OTHER_PRICE,
        "r0": REGIONS[0] if REGIONS else "",
        "r1": REGIONS[1] if len(REGIONS) > 1 else "",
        "r2": REGIONS[2] if len(REGIONS) > 2 else ""}


def fill(text):
    return text % FILL


def build(dialog, extra=""):
    return PAGE % (fill(dialog), fill(MASK), fill(BANNER), fill(DECOY),
                   extra + FOOTER)


def clicked(page):
    return page.evaluate("() => window.__clicked")


def checks(page, expect_dialog=True):
    """Run the three dialog steps and check what they did."""
    ok = True

    found = page.evaluate(DIALOG_FIND_JS,
                          [PANEL_SEL, MARKERS, list(CONFIRM_WORDS), DLG_TAG])
    if expect_dialog:
        if not found:
            print("  FAIL: no dialog was found")
            return False
        if PRICE_TAG not in found["text"]:
            print("  FAIL: the wrong panel was picked -- %r" % found["text"][:120])
            return False
        if "确认购买" not in found["buttons"]:
            print("  FAIL: the wrong panel was picked -- buttons=%s"
                  % found["buttons"])
            return False
        print("  ok: the dialog is found by its content -- %dx%d  buttons=%s"
              % (found["w"], found["h"], found["buttons"]))
    else:
        if found:
            print("  FAIL: a panel was found where there is no dialog -- %r"
                  % found["text"][:120])
            ok = False
        else:
            print("  ok: no dialog is found (as it should be)")

    region = page.evaluate(DIALOG_REGION_JS, [DLG_TAG, REGIONS])
    if expect_dialog:
        if region != REGIONS[0]:
            print("  FAIL: the region was %r, expected %r" % (region, REGIONS[0]))
            ok = False
        else:
            print("  ok: the region comes from inside the dialog -- %r" % region)
    elif region is not None:
        print("  FAIL: a region was picked without a dialog -- %r" % region)
        ok = False

    got = page.evaluate(DIALOG_CONFIRM_JS,
                        [DLG_TAG, list(DIALOG_CONFIRM), list(STRONG_CONFIRM)])
    hit = clicked(page)
    if expect_dialog:
        if not got:
            print("  FAIL: nothing was confirmed")
            ok = False
        elif got["text"] != "确认购买":
            print("  FAIL: confirmed %r, expected '确认购买'" % got["text"])
            ok = False
        else:
            print("  ok: the confirm button is %r, in %s" % (got["text"], got["where"]))
        if "确定" in hit:
            print("  FAIL: the stray 确定 was clicked: %s" % hit)
            ok = False
        else:
            print("  ok: the stray 确定 was NOT clicked -- clicked=%s" % hit)
    else:
        if got:
            print("  FAIL: something was confirmed without a dialog -- %r" % got)
            ok = False
        elif hit:
            print("  FAIL: something was clicked without a dialog -- %s" % hit)
            ok = False
        else:
            print("  ok: nothing is clicked when there is no dialog")
    return ok


def main():
    ok = True
    path = os.path.abspath("test_dialog_synthetic.html")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})

        # ---- 1. the normal case -----------------------------------------
        print("--- the dialog is on the page ---")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build(DIALOG))
        page.goto("file:///" + path.replace("\\", "/"), wait_until="load")
        ok = checks(page) and ok

        # ---- 2. the dialog's class is unknown ---------------------------
        print("\n--- the dialog's class is one we do not know ---")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build(DIALOG_ODD))
        page.goto("file:///" + path.replace("\\", "/"), wait_until="load")
        found = page.evaluate(DIALOG_FIND_JS,
                              [PANEL_SEL, MARKERS, list(CONFIRM_WORDS),
                               DLG_TAG])
        if found:
            print("  FAIL: a panel was matched by a class we do not know -- %r"
                  % found["text"][:120])
            ok = False
        else:
            print("  ok: no panel is matched by the unknown class")
        got = page.evaluate(DIALOG_CONFIRM_JS,
                            [DLG_TAG, list(DIALOG_CONFIRM), list(STRONG_CONFIRM)])
        hit = clicked(page)
        if got and got["text"] == "确认购买" and "确定" not in hit:
            print("  ok: the unique 确认购买 is clicked as a fallback -- %r"
                  % got["text"])
        else:
            print("  FAIL: the fallback did not click the order button -- %r %s"
                  % (got, hit))
            ok = False

        # ---- 3. no dialog at all ----------------------------------------
        print("\n--- there is no dialog (the click did not open one) ---")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build(""))
        page.goto("file:///" + path.replace("\\", "/"), wait_until="load")
        ok = checks(page, expect_dialog=False) and ok

        # ---- 4. the overlay carries a confirm button too ----------------
        print("\n--- the full-screen overlay has a 确定 of its own ---")
        mask_with_btn = fill(MASK).replace(
            "</div>\n</div>",
            '<button class="mask-ok">确定</button></div>\n</div>')
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build(DIALOG, extra="").replace(fill(MASK), mask_with_btn))
        page.goto("file:///" + path.replace("\\", "/"), wait_until="load")
        ok = checks(page) and ok

        # ---- 5. only the overlay ----------------------------------------
        print("\n--- only the full-screen overlay, no dialog ---")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build("", extra="").replace(fill(MASK), mask_with_btn))
        page.goto("file:///" + path.replace("\\", "/"), wait_until="load")
        ok = checks(page, expect_dialog=False) and ok

        browser.close()
    os.remove(path)
    print()
    print("all checks passed" if ok else "SOME CHECKS FAILED")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
