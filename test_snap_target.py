"""Offline test of the flash-sale button locator.

Builds a synthetic copy of the *current* activity page -- with the traps that
break a naive locator: a plain 109元 card that also says 4核4G3M, a module
whose cards are still skeletons (empty button text), a 138元 decoy, and the
look-alike progress class uno3-buy-card__btn-progress -- then checks that the
locator picks the 立即抢购 button of the 38元 4核4G card and nothing else.

No network, no account, no purchase: the page is a local string.

    .venv\\Scripts\\python.exe test_snap_target.py
"""
import sys

from playwright.sync_api import sync_playwright

from snap_target import (BTN_ATTR, BTN_CLASS, CARD_ATTR, GROUPS_JS, IDX_ATTR,
                         MARK_ATTR, MARKER, RESOLVE_JS, TARGET, pick, score)

from snap_config import (MARKER_TEXT, PRICE_NUMBER, PRICE_TEXT, SPEC_TEXT)

sys.stdout.reconfigure(encoding="utf-8")

CARD = """
<div class="uno3-buy-card uno3-buy-card-normal%(skel)s">
  <div class="uno3-buy-card__inner">
    <div class="uno3-buy-card__title">%(title)s</div>
    <div class="uno3-buy-card__spec">%(spec)s</div>
    <div class="uno3-buy-card__quota">%(quota)s</div>
    <div class="uno3-buy-card__price">活动价：%(price)s元</div>
    %(btn)s
  </div>
</div>
"""

BUTTON = '<div class="uno3-buy-card__btn">%(text)s</div>'


def card(title, spec, quota, price, text, skeleton=False, progress=False):
    btn = ""
    if progress:
        btn += '<div class="uno3-buy-card__btn-progress">0%% 已抢0%%</div>'
    if text:
        btn += BUTTON % {"text": text}
    return CARD % {"title": title, "spec": spec, "quota": quota, "price": price,
                   "btn": btn, "skel": " uno3-skeleton" if skeleton else ""}


def module(tabs, cards):
    """A product module: a tab bar plus a row of product cards."""
    tab_html = "".join(
        '<div class="uno3-buy-card-tab-item%s">'
        '<div class="uno3-buy-card-tab-item-text">%s</div></div>'
        % (" is-active" if i == 0 else "", t)
        for i, t in enumerate(tabs))
    card_html = "".join(
        '<div class="qc-base-grid__col">%s</div>' % c for c in cards)
    return ('<div class="qc-base-module uno3-buy-card-wrap">'
            '<div class="qc-base-grid__layout"><div class="qc-base-show-box">'
            '<div class="qc-base-show-mobile"><div class="uno3-buy-card-tab">%s'
            '</div></div></div>'
            '<div class="qc-base-grid__row">%s</div>'
            '</div></div>' % (tab_html, card_html))


# the 秒杀 module: its tab bar names PRODUCTS, not specs -- so a finder that
# looks for a tab mentioning the spec never reaches this module
SECKILL = module(
    ["轻量应用服务器", "轻量对象存储", "企业网盘", "云开发 CloudBase"],
    [card("轻量应用服务器", SPEC_TEXT + "3M", MARKER_TEXT + " 月流量",
          PRICE_NUMBER, "添加提醒", progress=True),
     card("轻量对象存储", "10GB", "1GB 外网下行流量", "18", "添加提醒"),
     card("企业网盘", "企业版", "10人", "9", "添加提醒")])

# the 首单特惠 module: the same spec at a different price, plus a skeleton
# card (no button at all) and a 138元 decoy
FIRST_ORDER = module(
    ["轻量应用服务器 " + SPEC_TEXT + "3M", "轻量应用服务器 4核8G5M"],
    [card("轻量应用服务器", SPEC_TEXT + "3M", MARKER_TEXT + " 月流量",
          "109", "立即购买"),
     card("轻量应用服务器", "2核2G3M", "不限流量", "9", "", skeleton=True),
     card("轻量应用服务器", "4核8G5M", "500GB 月流量", "138", "立即购买")])

# a module whose card does not print the spec -- only the tab does
AGENT = module(
    ["另一个商品 " + SPEC_TEXT + "3M"],
    [card("另一个商品", SPEC_TEXT, "不限流量", PRICE_NUMBER, "添加提醒")])

PAGE = """
<!doctype html><html><head><meta charset="utf-8"><style>
body { margin: 0; font-family: sans-serif; }
.uno3-buy-card { width: 300px; height: 260px; display: inline-block;
                 vertical-align: top; border: 1px solid #ccc; }
.uno3-buy-card__btn { width: 160px; height: 44px; background: #eee; }
.uno3-buy-card__btn-progress { width: 300px; height: 44px; background: #ddd; }
</style></head><body>

<div class="section">
  <h2>上云精选 · 限时秒杀</h2>
  %(seckill)s
</div>

<div class="section">
  <h2>首单特惠</h2>
  %(first)s
</div>

<div class="section">
  <h2>新客专享</h2>
  %(agent)s
</div>

</body></html>
""" % {"seckill": SECKILL, "first": FIRST_ORDER, "agent": AGENT}

failures = []


def check(name, ok, detail=""):
    print("  %-4s %s%s" % ("ok" if ok else "FAIL", name,
                           ("  -- " + detail) if detail else ""))
    if not ok:
        failures.append(name)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.set_content(PAGE, wait_until="load")

    cands = page.evaluate(RESOLVE_JS, [TARGET, MARKER, BTN_CLASS,
                                        ["添加提醒", "取消提醒", "立即抢购", "已抢光",
                                         "已结束", "正在处理", "已抢完"],
                                        IDX_ATTR, CARD_ATTR])
    groups = page.evaluate(GROUPS_JS, [TARGET, "限时秒杀"])
    browser.close()

print("candidates:")
for c in cands:
    print("  [%d] %-8r score=%d target=%-5s 38=%-5s marker=%-5s byTab=%-5s | %s"
          % (c["idx"], c["text"], score(c), c["hasTarget"], c["has38"],
             c["hasMarker"], c["byTab"], c["card"][:90]))

print()
print("checks:")

# 1. the progress bar is not a candidate
check("progress bar not a candidate",
      all(c["text"] != "0% 已抢0%" for c in cands))

# 2. the skeleton card (no button at all) is not a candidate
check("skeleton card not a candidate",
      all("2核2G3M" not in c["card"] for c in cands),
      str([c["card"][:40] for c in cands]))

# 3. the winner is the 38元 4核4G card
best = pick(cands)
check("a winner was found", best is not None)
if best:
    check("winner card has the spec", best["hasTarget"] or best["byTab"])
    check("winner card has %s" % MARKER_TEXT, best["hasMarker"],
          repr(best["card"][:80]))
    check("winner card is the %s one" % PRICE_TEXT, best["has38"],
          repr(best["card"][:80]))
    check("winner is not the 109元 card", "109" not in best["card"],
          best["card"][:80])
    check("winner is not the decoy card",
          ("1" + PRICE_NUMBER) not in best["card"], best["card"][:80])

# 4. the 38元 price must be what decides -- the 109元 card mentions the spec too
others = [c for c in cands
          if best is not None and c is not best and c["hasTarget"]
          and c["hasMarker"]]
check("the 109元 card scores lower", not others or score(others[0]) < score(best),
      "decoy=%d winner=%d" % (score(others[0]), score(best)) if others else "")

# 5. the price regex must not fire on 138元 / 109元
from snap_target import PRICE_38
check("a decoy price is not read as %s" % PRICE_TEXT,
      not PRICE_38.search("活动价：1" + PRICE_NUMBER + "元"))
check("another decoy is not read as %s" % PRICE_TEXT,
      not PRICE_38.search("活动价：2" + PRICE_NUMBER + "元"))
check("%s is read as %s" % (PRICE_TEXT, PRICE_TEXT),
      bool(PRICE_38.search("活动价：" + PRICE_TEXT)))

# 6. the module finder must reach the flash-sale module -- its tab bar names
#    products, not specs, so searching the tabs for the spec misses it
check("every module is listed", len(groups) == 3, str(len(groups)))
seckill = [g for g in groups if g["isSeckill"]]
check("the flash-sale module is found", len(seckill) == 1,
      str([g["title"] for g in groups]))
check("the flash-sale module is listed first",
      bool(groups) and groups[0]["isSeckill"], str([g["title"] for g in groups]))
check("the flash-sale module has no spec in its tabs",
      bool(seckill) and not seckill[0]["hasSpec"], str(seckill))

# ---- end to end: mark / reveal / click against the synthetic page -------
print()
print("end-to-end (mark -> reveal -> click):")

import snap_browser

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.set_content(PAGE, wait_until="load")
    page.evaluate(
        """
        () => {
            window.__clicked = [];
            document.querySelectorAll("div.uno3-buy-card__btn").forEach((b) => {
                b.addEventListener("click", () => {
                    const card = b.closest(".uno3-buy-card");
                    window.__clicked.push(
                        (card.textContent || "").includes("活动价：__PRICE__") ? "TARGET" : "OTHER");
                });
            });
        }""".replace("__PRICE__", PRICE_TEXT)
    )

    cands = snap_browser.scan(page)
    best = snap_browser.pick(cands)
    check("scan found the target", best is not None and best["text"] == "添加提醒",
          repr(best["text"]) if best else "none")

    snap_browser.mark(page, best["idx"])
    snap_browser.reveal(page, best["idx"])

    marked = page.evaluate(
        """
        () => [...document.querySelectorAll("[data-snap-target='1']")].map(
            (e) => (e.className || "") + "|" + (e.textContent || "").trim())
        """
    )
    check("two elements marked (button + card)", len(marked) == 2, str(marked))
    check("the marked button is the 添加提醒 one",
          any(m.startswith("uno3-buy-card__btn") and "添加提醒" in m for m in marked),
          str(marked))
    check("the marked card is the %s one" % PRICE_TEXT,
          any(("活动价：" + PRICE_TEXT) in m for m in marked), str(marked))
    check("the marked card is not the 109元 one",
          not any("活动价：109元" in m for m in marked), str(marked))

    btn = page.evaluate(
        """
        () => {
            const b = document.querySelector("[data-snap-btn='1']");
            return b ? (b.className || "") + "|" + (b.textContent || "").trim() : null;
        }
        """
    )
    check("the button carries data-snap-btn", btn is not None and "添加提醒" in btn,
          repr(btn))

    outlined = page.evaluate(
        """
        () => {
            const b = document.querySelector("[data-snap-btn='1']");
            return b ? b.style.outline : null;
        }
        """
    )
    check("the button itself is outlined", bool(outlined), repr(outlined))

    ok = snap_browser.click_target(page)
    clicked = page.evaluate("() => window.__clicked")
    check("click_target reported success", ok)
    check("the click landed on the %s card" % PRICE_TEXT,
          clicked == ["TARGET"], "clicked=%s" % clicked)

    # the label of the marked button is readable without a DOM scan
    check("button_text reads the label", snap_browser.button_text(page) == "添加提醒",
          repr(snap_browser.button_text(page)))

    browser.close()

print()
if failures:
    print("FAILED: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all checks passed")
