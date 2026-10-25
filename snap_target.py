"""Locator for the flash-sale buy button.

Kept apart from snap_browser.py so the element logic can be tested offline
against a synthetic copy of the page (see test_snap_target.py) without opening
a browser against the live site.

Nothing here knows about a particular product: the spec, the marker and the
price come from snap_config (config.json), and the price reaches the page as
a plain regex string.

**The page's structure:** every product group is a module

    div.qc-base-module.uno3-buy-card-wrap
      ├── div.uno3-buy-card-tab            (a tab per product variant)
      │     └── div.uno3-buy-card-tab-item[.is-active]
      │           └── div.uno3-buy-card-tab-item-text   "轻量应用服务器 4核4G3M"
      └── div.qc-base-grid__row            (one div.uno3-buy-card per variant)
            └── div.uno3-buy-card.uno3-buy-card-normal
                  └── div.uno3-buy-card__btn   添加提醒 / 立即抢购

Two traps:

* the card is a **skeleton** until its module is scrolled into view, so the
  button has no text at all and the whole group has to be on screen before
  the button can be found;
* the same card often exists in more than one module (the flash price in
  one, a normal price in another), so the price is what decides which one
  is ours.

The rule: the card must mention the spec, and it must be the cheap one.
"""
from snap_config import price_pattern, price_re  # noqa: F401  (re-exported)

BTN_CLASS = "div.uno3-buy-card__btn"
IDX_ATTR = "data-snap-idx"        # candidate index, set on the button
CARD_ATTR = "data-snap-card"      # candidate index, set on its card
MARK_ATTR = "data-snap-target"    # the winner (button + card)
BTN_ATTR = "data-snap-btn"        # the winner button only

# The labels the button goes through. They belong to the page, not to a
# campaign, but they do change when the page is rebuilt.
SEKILL_WORDS = ("添加提醒", "取消提醒", "立即抢购", "已抢光", "已结束",
                "正在处理", "已抢完")
BUY_WORDS = ("立即抢购",)
CONFIRM_WORDS = ("确认购买", "确认下单", "提交订单", "立即支付", "去支付",
                 "确定", "确认", "立即购买")


def is_buy_text(text):
    """Is this button label the one that actually opens the purchase?

    The button reads 添加提醒 before the session and 立即抢购 once it opens.
    Labels like 预约抢购 / 即将开抢 also contain 抢购 but are still pre-session,
    so anything mentioning 提醒 or 预约 is excluded.
    """
    if not text:
        return False
    if "提醒" in text or "预约" in text:
        return False
    return "抢购" in text or "马上抢" in text


def score(c):
    """How sure are we that this is the card we want? Higher is better."""
    s = 0
    if c.get("hasTarget"):
        s += 4
    if c.get("byTab"):
        s += 3
    if c.get("hasMarker"):
        s += 2
    if c.get("hasPrice"):
        s += 1
    return s


def pick(cands):
    """The best candidate: the card with our spec that carries our price.

    Several cards mention the spec. The one priced like the flash sale is
    the one we want; if any candidate carries that price, only those are
    considered.
    """
    pool = [c for c in cands
            if (c.get("hasTarget") or c.get("byTab"))
            and (c.get("hasMarker") or c.get("hasPrice"))]
    priced = [c for c in pool if c.get("hasPrice")]
    if priced:
        pool = priced
    best = None
    for c in pool:
        if best is None or score(c) > score(best):
            best = c
    return best


RESOLVE_JS = """
([target, marker, priceRe, words, idxAttr, cardAttr]) => {
    const norm = (s) => (s || "").replace(/\\s+/g, " ").trim();
    const tokens = (el) => String(el.className || "").split(/\\s+/);
    // the product card: a class token uno3-buy-card* without "__"
    const isCard = (el) => tokens(el).some(
        (t) => t.startsWith("uno3-buy-card") && !t.includes("__") &&
               t !== "uno3-buy-card-wrap" && t !== "uno3-buy-card-tab" &&
               t !== "uno3-buy-card-tab-item");
    const isWrap = (el) => tokens(el).includes("uno3-buy-card-wrap");
    // the flash price -- the pattern is built in Python and arrives ready
    const PRICE = new RegExp(priceRe);

    // The section title above a module ("上云精选 · 限时秒杀").
    //
    // Two traps: the card has its OWN title element (uno3-buy-card__title),
    // which must never be mistaken for the section title, and the section
    // title is usually an <h2> that sits OUTSIDE the module, next to it.
    // So: real headings win over class-based ones, and anything inside the
    // module (or inside any card) is excluded outright.
    const titleOf = (el) => {
        const before = (a, b) => (a.compareDocumentPosition(b) & 4) !== 0;
        const isHead = (h) => /^H[1-4]$/.test(h.tagName);
        let node = el, hops = 0;
        while (node && hops < 40) {
            node = node.parentElement; hops++;
            if (!node) break;
            const all = [...node.querySelectorAll(
                "h1,h2,h3,h4,[class*=sec-title],[class*=module-title],[class*=title]")];
            const out = all.filter((h) => !el.contains(h) &&
                                         !h.closest("[class*=buy-card]"));
            const order = (l) => {
                const p = l.filter((h) => before(h, el));
                return p.length ? p : l;
            };
            const heads = order(out.filter(isHead));
            if (heads.length) return norm(heads[0].textContent).slice(0, 40);
            const cls = order(out.filter((h) => !isHead(h)));
            if (cls.length) return norm(cls[0].textContent).slice(0, 40);
        }
        return "";
    };

    const btns = Array.from(
        document.querySelectorAll("[class*=buy-card__btn]:not([class*=progress])"));

    const out = [];
    btns.forEach((b) => {
        const t = norm(b.textContent);
        if (!t) return;                       // skeleton: the card has no data yet

        // nearest ancestor that is a product card
        let card = b, hops = 0;
        while (card.parentElement && hops < 12) {
            card = card.parentElement; hops++;
            if (isCard(card)) break;
        }
        if (!isCard(card)) return;

        // the module (tab bar + grid) the card sits in
        let group = card.parentElement, ghops = 0;
        while (group && ghops < 12) {
            if (isWrap(group)) break;
            group = group.parentElement; ghops++;
        }

        const cardText = norm(card.textContent);
        const tabs = group
            ? [...group.querySelectorAll("[class*=buy-card-tab-item-text]")]
                .map((e) => norm(e.textContent))
            : [];
        const groupCards = group
            ? [...group.querySelectorAll("[class*=buy-card]")].filter(isCard)
            : [];

        const title = group ? titleOf(group) : "";

        // tab order and card order describe the same product list
        let byTab = false;
        if (tabs.length) {
            const ti = tabs.findIndex((x) => x.includes(target));
            const ci = groupCards.indexOf(card);
            byTab = ti >= 0 && ti === ci;
        }

        const r = b.getBoundingClientRect();
        const idx = out.length;
        b.setAttribute(idxAttr, String(idx));
        card.setAttribute(cardAttr, String(idx));
        out.push({
            idx: idx,
            text: t,
            known: words.some((w) => t === w || t.includes(w)),
            card: cardText.slice(0, 1200),
            hasTarget: cardText.includes(target),
            hasMarker: cardText.includes(marker),
            hasPrice: PRICE.test(cardText),
            byTab: byTab,
            title: title,
            tabs: tabs.join(" / ").slice(0, 300),
            top: Math.round(r.top + window.scrollY),
            left: Math.round(r.left),
            width: Math.round(r.width),
            height: Math.round(r.height),
        });
    });
    return out;
}
"""

# Every product module on the page, the flash-sale one first.
#
# The flash-sale module has a tab bar of PRODUCT NAMES -- 轻量应用服务器 |
# 轻量对象存储 | 企业网盘 | 云开发 CloudBase -- so looking for a module whose
# tab mentions the spec never finds it. Every module is therefore listed, the
# flash-sale one first, and each is scrolled into view and scanned in turn.
GROUPS_JS = """
([target, titleWord]) => {
    const norm = (s) => (s || "").replace(/\\s+/g, " ").trim();

    // the section title above a module -- see the note in RESOLVE_JS: the
    // card's own title element must not be mistaken for it
    const titleOf = (el) => {
        const before = (a, b) => (a.compareDocumentPosition(b) & 4) !== 0;
        const isHead = (h) => /^H[1-4]$/.test(h.tagName);
        let node = el, hops = 0;
        while (node && hops < 40) {
            node = node.parentElement; hops++;
            if (!node) break;
            const all = [...node.querySelectorAll(
                "h1,h2,h3,h4,[class*=sec-title],[class*=module-title],[class*=title]")];
            const out = all.filter((h) => !el.contains(h) &&
                                         !h.closest("[class*=buy-card]"));
            const order = (l) => {
                const p = l.filter((h) => before(h, el));
                return p.length ? p : l;
            };
            const heads = order(out.filter(isHead));
            if (heads.length) return norm(heads[0].textContent).slice(0, 40);
            const cls = order(out.filter((h) => !isHead(h)));
            if (cls.length) return norm(cls[0].textContent).slice(0, 40);
        }
        return "";
    };

    const out = [];
    document.querySelectorAll("[class*=buy-card-wrap]").forEach((g) => {
        const tabs = [...g.querySelectorAll("[class*=buy-card-tab-item-text]")]
            .map((e) => norm(e.textContent));
        const title = titleOf(g);
        const r = g.getBoundingClientRect();
        out.push({
            top: Math.round(r.top + window.scrollY),
            tabs: tabs.join(" / ").slice(0, 200),
            title: title,
            hasSpec: tabs.some((t) => t.includes(target)),
            isSeckill: title.includes(titleWord),
        });
    });
    out.sort((a, b) => (b.isSeckill - a.isSeckill) || (b.hasSpec - a.hasSpec));
    return out;
}
"""

MARK_JS = """
([idx, idxAttr, cardAttr, markAttr, btnAttr]) => {
    const b = document.querySelector("[" + idxAttr + "='" + idx + "']");
    if (!b) return false;
    b.setAttribute(markAttr, "1");
    b.setAttribute(btnAttr, "1");
    const c = document.querySelector("[" + cardAttr + "='" + idx + "']");
    if (c) c.setAttribute(markAttr, "1");
    return true;
}
"""
