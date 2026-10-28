"""Offline test of the buy-dialog handling.

The dialog is the one part of the buyer that has never been seen for real:
on the first live attempt the confirm step clicked a stray 确定 somewhere else
on the page, the dialog closed, and no order was ever created. So the rules
are pinned down here against a synthetic page that carries every trap:

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

No network, no account, no purchase: the page is a local string.

    .venv\\Scripts\\python.exe test_dialog.py
"""
import os
import sys

from playwright.sync_api import sync_playwright

from snap_browser import (BUY_MARKERS, CONFIRM_WORDS, DIALOG_CONFIRM,
                          DIALOG_CONFIRM_JS, DIALOG_FIND_JS, DIALOG_REGION_JS,
                          DLG_TAG, PANEL_ONLY, PANEL_SEL, PRICE_RE,
                          REGION_WORDS, STRONG_CONFIRM)
from snap_config import (MARKER_TEXT, PRICE_NUMBER, PRICE_TEXT,
                         REGION_WORDS, SPEC_TEXT)

sys.stdout.reconfigure(encoding="utf-8")

# The dialog, drawn like the real one: the product line, the price, the
# region, and a footer with a cancel and a confirm button.
DIALOG = """
<div class="qc-modal buy-dialog" style="position:fixed;left:200px;top:120px;
     width:520px;background:#fff;padding:16px;z-index:10">
  <div class="buy-dialog__title">确认订单</div>
  <div class="buy-dialog__body">
    <div class="buy-dialog__goods">轻量应用服务器 __SPEC__3M __QUOTA__ 月流量</div>
    <div class="buy-dialog__price">活动价：__PRICE__</div>
    <div class="buy-dialog__region">地域：
      <span class="region-item">__R0__</span>
      <span class="region-item">__R1__</span>
      <span class="region-item">__R2__</span>
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

# Our dialog with BOTH prices on it: 活动价：38元 AND 日常价：780元. That is
# what the real panel looks like -- the card itself quotes both. The b5
# rule "more than one price on the box => not a dialog" threw our own
# panel away, and on 2026-10-27 the run could not tell a missed dialog
# from a missing one.
DIALOG_TWO_PRICES = """
<div class="qc-modal buy-dialog" style="position:fixed;left:200px;top:120px;
     width:520px;background:#fff;padding:16px;z-index:10">
  <div class="buy-dialog__title">确认订单</div>
  <div class="buy-dialog__goods">轻量应用服务器 __SPEC__3M __QUOTA__ 月流量</div>
  <div class="buy-dialog__price">活动价：__PRICE__ 日常价：__DAILY__</div>
  <div class="buy-dialog__region">地域：
    <span class="region-item">__R0__</span>
  </div>
  <div class="buy-dialog__footer">
    <button class="btn btn-cancel">取消</button>
    <button class="btn btn-ok">确认购买</button>
  </div>
</div>
"""

# A full-screen overlay that happens to contain the product card: it names our
# product and it is huge, so it must never be taken for the dialog.
MASK = """
<div class="activity-mask" style="position:fixed;inset:0;background:rgba(0,0,0,.4);
     z-index:5">
  <div class="uno3-buy-card uno3-buy-card-normal">
    <div class="uno3-buy-card__spec">__SPEC__3M __QUOTA__ 月流量</div>
    <div class="uno3-buy-card__price">活动价：__PRICE__</div>
    <div class="uno3-buy-card__btn">立即抢购</div>
  </div>
</div>
"""

# A promo banner that names our product but carries no confirm button.
BANNER = """
<div class="promo-panel" style="margin:40px">
  <div>__SPEC__3M __PRICE__ 限量特惠</div>
  <a href="#">了解更多</a>
</div>
"""

# Another product's panel, with its own confirm button.
DECOY = """
<div class="other-modal" style="margin:20px;width:300px">
  <div>轻量对象存储 企业网盘 109元</div>
  <button>确定</button>
</div>
"""

FOOTER = """
<div class="page-footer" style="margin:40px">
  <button>确定</button>
</div>
"""

# A ROW of other products, one of which is a 4核4G3M -- but at 109元, and
# next to a 2核2G4M. On 2026-10-26 this row was mistaken for our buy dialog:
# it mentions our spec, so the finder tagged it, and the script then hunted
# 25 seconds for a confirm button that a product row does not have. The
# order was lost. A product row must never be taken for a dialog.
PRODUCT_ROW = """
<div class="hot-panel" style="margin:20px;width:1420px;display:flex;gap:20px">
  <div class="uno3-buy-card uno3-buy-card-normal">
    <div class="uno3-buy-card__spec">轻量应用服务器 __SPEC__3M 多 Agent/workspace</div>
    <div class="uno3-buy-card__price">活动价：109元 日常价：780元</div>
    <button class="uno3-buy-card__btn">立即购买</button>
  </div>
  <div class="uno3-buy-card uno3-buy-card-normal">
    <div class="uno3-buy-card__spec">轻量应用服务器 2核2G4M</div>
    <div class="uno3-buy-card__price">活动价：99元 日常价：576元</div>
    <button class="uno3-buy-card__btn">立即购买</button>
  </div>
</div>
"""

# OUR dialog, but this time the page's call to action reads 立即购买 instead
# of 确认购买. That is the label of the other cards' buttons, so it is only
# safe to click inside a panel the price gate has already verified.
DIALOG_CTA_BUY = """
<div class="qc-modal buy-dialog" style="position:fixed;left:200px;top:120px;
     width:520px;background:#fff;padding:16px;z-index:10">
  <div class="buy-dialog__title">确认订单</div>
  <div class="buy-dialog__goods">轻量应用服务器 __SPEC__3M __QUOTA__ 月流量</div>
  <div class="buy-dialog__price">活动价：__PRICE__</div>
  <div class="buy-dialog__region">地域：
    <span class="region-item">__R0__</span>
  </div>
  <div class="buy-dialog__footer">
    <button class="btn btn-cancel">取消</button>
    <button class="btn btn-ok">立即购买</button>
  </div>
</div>
"""

# Our dialog, but the only way forward is 立即支付. Paying is the user's
# job, always -- the script must not touch it, not even inside our own
# panel.
DIALOG_PAY = """
<div class="qc-modal buy-dialog" style="position:fixed;left:200px;top:120px;
     width:520px;background:#fff;padding:16px;z-index:10">
  <div class="buy-dialog__title">确认订单</div>
  <div class="buy-dialog__goods">轻量应用服务器 __SPEC__3M __QUOTA__ 月流量</div>
  <div class="buy-dialog__price">活动价：__PRICE__</div>
  <div class="buy-dialog__footer">
    <button class="btn btn-ok">立即支付</button>
  </div>
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


def build(dialog, extra=""):
    return PAGE % (dialog, MASK, BANNER, DECOY, extra + FOOTER)


def clicked(page):
    return page.evaluate("() => window.__clicked")


def checks(page, expect_dialog=True, expect_where="our dialog"):
    """Run the three dialog steps and check what they did."""
    ok = True

    found = page.evaluate(DIALOG_FIND_JS,
                          [PANEL_SEL, list(BUY_MARKERS), list(CONFIRM_WORDS),
                           DLG_TAG])
    if expect_dialog:
        if not found:
            print("  FAIL: no dialog was found")
            return False
        if PRICE_TEXT not in found["text"]:
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

    region = page.evaluate(DIALOG_REGION_JS, [DLG_TAG, list(REGION_WORDS)])
    if expect_dialog:
        if region != REGION_WORDS[0]:
            print("  FAIL: the region was %r, expected %r"
                  % (region, REGION_WORDS[0]))
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
        # b6 looks for the panel by its CONTENT, so an unknown class name
        # is no longer a problem: the dialog is found, and its own 确认购买
        # is the button that gets clicked -- the stray 确定 is still left
        # alone. (b5 could not do this: it only knew a list of class names.)
        found = page.evaluate(DIALOG_FIND_JS,
                              [PANEL_SEL, list(BUY_MARKERS), list(CONFIRM_WORDS),
                               DLG_TAG])
        if not found:
            print("  FAIL: the dialog was not found, though its class is odd")
            ok = False
        elif PRICE_TEXT not in found["text"]:
            print("  FAIL: the wrong panel was picked -- %r" % found["text"][:120])
            ok = False
        else:
            print("  ok: the dialog is found by its content -- %dx%d buttons=%s"
                  % (found["w"], found["h"], found["buttons"]))
        got = page.evaluate(DIALOG_CONFIRM_JS,
                            [DLG_TAG, list(DIALOG_CONFIRM), list(STRONG_CONFIRM)])
        hit = clicked(page)
        if got and got["text"] == "确认购买" and "确定" not in hit:
            print("  ok: its own 确认购买 is clicked -- %r" % got["text"])
        else:
            print("  FAIL: the order button was not clicked -- %r %s" % (got, hit))
            ok = False

        # ---- 3. no dialog at all ----------------------------------------
        print("\n--- there is no dialog (the click did not open one) ---")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build(""))
        page.goto("file:///" + path.replace("\\", "/"), wait_until="load")
        ok = checks(page, expect_dialog=False) and ok

        # ---- 4. the overlay carries a confirm button too ----------------
        print("\n--- the full-screen overlay has a 确定 of its own ---")
        mask_with_btn = MASK.replace(
            "</div>\n</div>",
            '<button class="mask-ok">确定</button></div>\n</div>')
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build(DIALOG, extra="").replace(MASK, mask_with_btn))
        page.goto("file:///" + path.replace("\\", "/"), wait_until="load")
        ok = checks(page) and ok

        # ---- 5. only the overlay ----------------------------------------
        print("\n--- only the full-screen overlay, no dialog ---")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build("", extra="").replace(MASK, mask_with_btn))
        page.goto("file:///" + path.replace("\\", "/"), wait_until="load")
        ok = checks(page, expect_dialog=False) and ok

        # ---- 6. a row of OTHER products ----------------------------------
        # This is the one that cost us the 2026-10-26 order: the row
        # mentions our spec, so it used to be tagged as our buy dialog.
        print("\n--- a row of other products (one is ours, at another price) ---")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build("", extra=PRODUCT_ROW))
        page.goto("file:///" + path.replace("\\", "/"), wait_until="load")
        found = page.evaluate(DIALOG_FIND_JS,
                              [PANEL_SEL, list(BUY_MARKERS), list(CONFIRM_WORDS),
                               DLG_TAG, PRICE_RE])
        if found:
            print("  FAIL: a product row was taken for the buy dialog -- %r"
                  % found["text"][:120])
            ok = False
        else:
            print("  ok: a product row is not mistaken for the buy dialog")
        # and with no price gate it must still be refused, on the row rule
        found = page.evaluate(DIALOG_FIND_JS,
                              [PANEL_SEL, list(BUY_MARKERS), list(CONFIRM_WORDS),
                               DLG_TAG])
        if found:
            print("  FAIL: a product row was taken for the buy dialog (no "
                  "price gate) -- %r" % found["text"][:120])
            ok = False
        else:
            print("  ok: a product row is refused on its own, too")
        got = page.evaluate(DIALOG_CONFIRM_JS,
                            [DLG_TAG, list(DIALOG_CONFIRM), list(STRONG_CONFIRM),
                             list(PANEL_ONLY)])
        hit = clicked(page)
        if got or hit:
            print("  FAIL: something was clicked in a product row -- %r %s"
                  % (got, hit))
            ok = False
        else:
            print("  ok: nothing is clicked in a product row")

        # ---- 7. our dialog, but the CTA reads 立即购买 --------------------
        print("\n--- our dialog, but the button says 立即购买 ---")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build(DIALOG_CTA_BUY))
        page.goto("file:///" + path.replace("\\", "/"), wait_until="load")
        found = page.evaluate(DIALOG_FIND_JS,
                              [PANEL_SEL, list(BUY_MARKERS), list(CONFIRM_WORDS),
                               DLG_TAG, PRICE_RE])
        if not found:
            print("  FAIL: our dialog was not found")
            ok = False
        else:
            print("  ok: our dialog is found -- buttons=%s" % found["buttons"])
        got = page.evaluate(DIALOG_CONFIRM_JS,
                            [DLG_TAG, list(DIALOG_CONFIRM), list(STRONG_CONFIRM),
                             list(PANEL_ONLY)])
        hit = clicked(page)
        if got and got["text"] == "立即购买":
            print("  ok: the panel's own 立即购买 is clicked -- %s" % got["where"])
        else:
            print("  FAIL: the panel's 立即购买 was not clicked -- %r %s"
                  % (got, hit))
            ok = False

        # ---- 8. the only way on is 立即支付 ------------------------------
        print("\n--- the only button is 立即支付 (paying is the user's job) ---")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build(DIALOG_PAY))
        page.goto("file:///" + path.replace("\\", "/"), wait_until="load")
        got = page.evaluate(DIALOG_CONFIRM_JS,
                            [DLG_TAG, list(DIALOG_CONFIRM), list(STRONG_CONFIRM),
                             list(PANEL_ONLY)])
        hit = clicked(page)
        if got:
            print("  FAIL: the script clicked %r -- it must never pay"
                  % got["text"])
            ok = False
        elif hit:
            print("  FAIL: the script clicked %s -- it must never pay" % hit)
            ok = False
        else:
            print("  ok: 立即支付 was NOT clicked -- nothing was clicked")

        # ---- 9. our dialog shows BOTH prices ----------------------------
        # This is what the real panel looks like. b5 refused it (two prices
        # on the box => "not a dialog"), so our own panel was thrown away
        # and the run could not tell a missed dialog from a missing one.
        print("\n--- our dialog shows both prices ---")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build(DIALOG_TWO_PRICES))
        page.goto("file:///" + path.replace("\\", "/"), wait_until="load")
        found = page.evaluate(DIALOG_FIND_JS,
                              [PANEL_SEL, list(BUY_MARKERS), list(CONFIRM_WORDS),
                               DLG_TAG, PRICE_RE])
        if not found:
            print("  FAIL: our own dialog was thrown away because it quotes "
                  "two prices")
            ok = False
        else:
            print("  ok: the dialog is found -- buttons=%s" % found["buttons"])
        got = page.evaluate(DIALOG_CONFIRM_JS,
                            [DLG_TAG, list(DIALOG_CONFIRM), list(STRONG_CONFIRM),
                             list(PANEL_ONLY)])
        hit = clicked(page)
        if got and got["text"] == "确认购买" and "确定" not in hit:
            print("  ok: its own 确认购买 is clicked -- %r" % got["text"])
        else:
            print("  FAIL: the order button was not clicked -- %r %s" % (got, hit))
            ok = False

        browser.close()
    os.remove(path)
    print()
    print("all checks passed" if ok else "SOME CHECKS FAILED")
    raise SystemExit(0 if ok else 1)


# The sample pages are written with placeholders and filled in from the
# configuration, so they follow whatever product the reader sets up.
for _name in ("DIALOG", "DIALOG_ODD", "MASK", "BANNER", "PRODUCT_ROW",
              "DIALOG_CTA_BUY", "DIALOG_PAY", "DIALOG_TWO_PRICES"):
    globals()[_name] = (globals()[_name]
                        .replace("__SPEC__", SPEC_TEXT)
                        .replace("__PRICE__", PRICE_TEXT)
                        .replace("__QUOTA__", MARKER_TEXT)
                        .replace("__DAILY__",
                                 "%d元" % (int(PRICE_NUMBER) * 20))
                        .replace("__R0__", REGION_WORDS[0])
                        .replace("__R1__", REGION_WORDS[1 % len(REGION_WORDS)])
                        .replace("__R2__", REGION_WORDS[2 % len(REGION_WORDS)]))

if __name__ == "__main__":
    main()
