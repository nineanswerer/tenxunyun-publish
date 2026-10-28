"""Buy the 4核4G / 38元 lighthouse flash-sale server straight from the live page.

Why a browser and not snap_up_server.py: the live page signs its purchase
request (the request layer computes a signature from the server clock, the
request body, the user agent and the skey cookie). The plain-HTTP script
cannot produce that signature, so a raw POST would be rejected. The browser
already carries the signing code, so letting the page make its own request is
both simpler and more reliable.

The button is the element the user pointed at:

    <div class="uno3-buy-card__btn">添加提醒</div>

which becomes

    <div class="uno3-buy-card__btn">立即抢购</div>

while the session runs, and 已抢光 once the stock is gone. Which card it is,
how it is marked and how it is clicked all live in snap_target.py -- see
test_snap_target.py for the offline proof that the right button is chosen.

Flow:
  1. open the activity page with the saved cookies
  2. read the flash-sale schedule out of the page (which session is running,
     which one is next)
  3. scroll the module that offers 4核4G into view -- the cards are skeletons
     until then -- and find the 38元 card's button
  4. when that button says 立即抢购, click it and confirm the buy dialog
  5. leave the browser open so the order can be paid

Every request is logged as METHOD host/path plus the KEY NAMES of its body.
No headers, no cookies, no response bodies are recorded.
"""
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import snap_report
from playwright.sync_api import sync_playwright

from snap_config import (ACTIVITY_URL, BUY_MARKERS, PRICE_RE, PRICE_TEXT,
                         REGION_WORDS, SECKILL_TITLE, SPEC_TEXT)

from snap_target import (BTN_ATTR, BTN_CLASS, BUY_WORDS, CARD_ATTR,
                         CONFIRM_WORDS, GROUPS_JS, IDX_ATTR, MARK_ATTR,
                         MARKER, MARK_JS, RESOLVE_JS, SEKILL_WORDS, TARGET,
                         is_buy_text, pick, score)

sys.stdout.reconfigure(encoding="utf-8")

PAGE_URL = ACTIVITY_URL
LOG = "snap_browser.log"
HOST_RE = re.compile(r"\.tencent\.com$")
BEIJING = timezone(timedelta(hours=8))
# printed at START so it is always obvious which build is running
BUILD = ("b7: the click goes out a moment before the session opens and is "
         "repeated the moment it opens, the server's answer to the order "
         "request is always recorded, the buy panel is found by its content "
         "instead of by its class, and every run writes a report")

# How early the click may go out, in seconds. The button arms a moment
# before the sale opens, and on 2026-10-28 a click that landed exactly on
# the opening was too late: the server answered "sold out" three seconds
# later. So the click goes out EARLY_LEAD seconds before the opening, and
# is repeated the moment the sale is open (see handle_purchase).
EARLY_LEAD = 1.0

# A panel is "ours" only if it names our product -- otherwise the confirm
# search clicks a stray 确定 somewhere else on the page, which is what
# happened on the first attempt: the dialog opened, a random 确定 closed it,
# and no order was ever created.

# The price, as a regular expression, used as the gate on the buy panel:
# only a panel that shows THIS price is ours. 38元 must not match 138元 or
# 38.5元. (Built here so the JS never has to escape anything itself.)

# The only request bodies worth recording: the flash-sale traffic. The page
# also fires one check-available / query-price pair per card on the page,
# which is a couple of hundred identical schema dumps and nothing else.
INTEREST = ("seckill", "do-goods", "do-voucher", "prepare-do",
            "to-settle", "add-cart", "check-goods")

logf = None


def note(tag, msg):
    line = "[%s] %-5s %s" % (time.strftime("%H:%M:%S"), tag, msg)
    print(line, flush=True)
    if logf:
        logf.write(line + "\n")
    # the report keeps its own copy of the timeline, so a run can be
    # explained without digging through the log
    try:
        snap_report.get().event(tag, msg)
    except Exception:
        pass


# --------------------------------------------------------------------------
# element handling (importable, so it can be tested offline)
# --------------------------------------------------------------------------
def scan(page):
    """All seckill candidates, each with the text of its own card."""
    return page.evaluate(RESOLVE_JS, [TARGET, MARKER, BTN_CLASS,
                                      list(SEKILL_WORDS), IDX_ATTR, CARD_ATTR])


def mark(page, idx):
    """Tag the winning button and its card."""
    return page.evaluate(MARK_JS, [idx, IDX_ATTR, CARD_ATTR, MARK_ATTR,
                                   BTN_ATTR])


def reveal(page, idx):
    """Scroll the target into view and outline the button and its card."""
    return page.evaluate(
        """
        ([idx, idxAttr, cardAttr]) => {
            const b = document.querySelector("[" + idxAttr + "='" + idx + "']");
            if (!b) return false;
            b.scrollIntoView({block: "center"});
            b.style.outline = "3px solid #ff2d55";
            b.style.outlineOffset = "2px";
            b.style.background = "#ffe6ec";
            const c = document.querySelector("[" + cardAttr + "='" + idx + "']");
            if (c) {
                c.style.outline = "3px dashed #ff9500";
                c.style.outlineOffset = "4px";
            }
            return true;
        }
        """,
        [idx, IDX_ATTR, CARD_ATTR],
    )


def button_text(page):
    """The label of the marked button -- one element lookup, no DOM scan."""
    try:
        el = page.locator("[%s='1']" % BTN_ATTR).first
        if el.count() == 0:
            return None
        return (el.inner_text() or "").strip()
    except Exception:
        return None


def click_target(page):
    """Real mouse click on the centre of the target button.

    The button that is ALREADY marked wins: when the session opens there is
    no time to re-scan the page, and a scan only sees the cards whose module
    is on screen. Re-picking is the fallback for when the mark was lost.
    """
    el = None
    try:
        el = page.locator("[%s='1']" % BTN_ATTR).first
        if el.count() == 0:
            el = None
    except Exception:
        el = None
    if el is None:
        try:
            best = pick(scan(page))
        except Exception:
            return False
        if not best or not mark(page, best["idx"]):
            return False
        el = page.locator("[%s='1']" % BTN_ATTR).first
        if el.count() == 0:
            return False
    try:
        el.scroll_into_view_if_needed(timeout=4000)
        box = el.bounding_box()
        if not box:
            return False
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        return True
    except Exception as exc:
        note("FIRE", "click error: %s" % str(exc)[:200])
        return False


def click_target_js(page):
    """Click the marked button without hit-testing.

    A real mouse click lands on whatever covers the button. When the first
    click was refused, the page may put a mask over the card -- and a click
    on that mask closes the buy panel we just opened. This one goes
    straight to the button's own handler.
    """
    try:
        return bool(page.evaluate(
            "() => { const e = document.querySelector('[%s=\"1\"]');"
            " if (!e) return false; e.click(); return true; }" % BTN_ATTR))
    except Exception:
        return False


# --------------------------------------------------------------------------
# finding the card
# --------------------------------------------------------------------------
def find_target(page, budget=40.0):
    """Bring the modules into view one by one and scan them.

    The page renders each module's cards as skeletons until the module is on
    screen, so a scan taken at the bottom of the page sees nothing. The
    flash-sale module is tried first -- it is the one that sells the 38元
    card, and its tab bar names products, not specs.
    """
    end = time.time() + budget
    try:
        groups = page.evaluate(GROUPS_JS, [TARGET, SECKILL_TITLE])
    except Exception as exc:
        note("SCAN", "module lookup failed: %s" % str(exc)[:120])
        return None
    note("SCAN", "%d modules on the page" % len(groups))
    for g in groups:
        page.evaluate("window.scrollTo(0, %d)" % max(0, g["top"] - 250))
        page.wait_for_timeout(1200)
        while time.time() < end:
            try:
                cands = scan(page)
            except Exception:
                cands = []
            best = pick(cands)
            if best is not None:
                note("SCAN", "module [%s] -> %r score=%d"
                     % (g["title"] or g["tabs"][:36], best["text"], score(best)))
                return best
            page.wait_for_timeout(400)
    return None


def remark(page):
    """Re-mark the target without moving the page (it is usually still there).

    React may replace the button node when its label changes, which drops the
    mark attribute; a plain rescan is enough then -- no scrolling, so the user
    keeps the page they are looking at.
    """
    try:
        c = pick(scan(page))
    except Exception:
        return None
    if c is None or not mark(page, c["idx"]):
        return None
    return c


def survey(page, budget=25.0):
    """Every 4核4G card on the page and what its button says right now.

    The scan returns EVERY button on the page, so the same card comes back
    once per module -- it is de-duplicated here, otherwise the log fills up
    with copies of the same three lines.
    """
    try:
        groups = page.evaluate(GROUPS_JS, [TARGET, SECKILL_TITLE])
    except Exception:
        return []
    out = []
    seen = set()
    for g in groups:
        page.evaluate("window.scrollTo(0, %d)" % max(0, g["top"] - 250))
        page.wait_for_timeout(1200)
        try:
            cands = scan(page)
        except Exception:
            continue
        for c in cands:
            if not (c.get("hasTarget") or c.get("byTab")):
                continue
            # the key must NOT include `top`: lazy-loaded content changes the
            # document height, so the same card's absolute position moves
            # between two scans and the de-duplication misses it
            key = (c["text"], c["card"][:80], c.get("title") or "")
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
    return out


# --------------------------------------------------------------------------
# the flash-sale schedule
# --------------------------------------------------------------------------
SESSION_JS = """
() => {
    const el = document.getElementById("__NEXT_DATA__");
    if (!el) return [];
    let data;
    try { data = JSON.parse(el.textContent); } catch (e) { return []; }
    const act = (data.props && data.props.pageProps &&
                 data.props.pageProps.activityData) || {};
    const st = act.twoDaySeckillState || {};
    const out = [];
    for (const [date, list] of Object.entries(st.flashResult || {})) {
        for (const s of list) {
            out.push({
                date: date,
                id: s.id,
                start: s.start_time || "",
                end: s.end_time || "",
                status: s.dynamic_status,
                status_cn: s.dynamic_status_cn,
                nowTime: st.nowTime,
                goods: (s.sales || []).map((g) => ({
                    act_id: g.act_id,
                    name: g.goods_name,
                    skus: (g.seckill_act_extra || {}).skuList || [],
                    price: (g.$goods || {}).total_num,
                })),
            });
        }
    }
    return out;
}
"""


def session_schedule(page):
    """Every flash-sale session the page knows about."""
    try:
        return page.evaluate(SESSION_JS)
    except Exception:
        return []


def session_start(s):
    """When does this session open? (Beijing time, absolute)"""
    try:
        return datetime.strptime("%s %s" % (s["date"], s["start"]),
                                  "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING)
    except Exception:
        return None


def session_end(s):
    try:
        return datetime.strptime("%s %s" % (s["date"], s["end"]),
                                  "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING)
    except Exception:
        return None


def ours(session, act_id):
    """Does this session sell our goods?"""
    for g in session.get("goods") or []:
        if str(g.get("act_id")) == str(act_id):
            return True
    return False


def describe(sessions, act_id):
    """Our session -- prefer one that is still running or still to come."""
    mine = [s for s in sessions if ours(s, act_id)]
    for s in mine:
        if s.get("status") != 2:
            return s
    return mine[0] if mine else None


def next_opening(scheduled, now=None):
    """When does our next session open? (a local timestamp, or None)

    `scheduled` is a list of (start_datetime, session) pairs, earliest
    first, for the sessions that sell our goods.

    Only a session that has NOT opened yet is worth waiting for: the
    button arms a moment before the opening, and a click that lands too
    early is refused by the server ("not started") -- the order is gone.
    A session that is running right now must be clicked straight away, and
    one that is over is skipped.

    Returns None when there is nothing to wait for.
    """
    now = time.time() if now is None else now
    for st, s in scheduled:
        en = session_end(s)
        if en and en.timestamp() <= now:
            continue                        # this one is over
        if st.timestamp() <= now:
            return None                    # this one is running: click now
        return st.timestamp()               # this one opens later: wait
    return None


def should_hold(flip_at, open_at, now=None):
    """Hold the click back until the session opens?

    The button arms a moment before the sale opens, and a click that
    lands too early is refused by the server ("not started") -- so the
    click waits for the opening.

    But only when the button armed just now: if it has been armed for a
    while, the sale is open and every second counts.
    """
    now = time.time() if now is None else now
    if not open_at or flip_at is None:
        return False
    if open_at - now <= 0:
        return False
    return (now - flip_at) <= 30


def release_in(open_at, now=None):
    """How many more seconds the click has to be held back (0 = let it go).

    On 2026-10-28 the click went out exactly on the opening and the server
    answered "sold out" three seconds later -- the goods were gone in the
    time it took to get the answer. Being first in the queue is worth more
    than being exactly on time, so the click is released EARLY_LEAD seconds
    BEFORE the opening: the request is queued first, and if it is refused
    for being early the click is repeated the moment the sale opens.

    With no opening time there is nothing to wait for: the click goes out.
    """
    now = time.time() if now is None else now
    if not open_at:
        return 0
    left = open_at - now - EARLY_LEAD
    return left if left > 0 else 0


# --------------------------------------------------------------------------
# the buy dialog
# --------------------------------------------------------------------------
# The dialog's class names are not known -- the page was rebuilt for the 双11
# campaign -- so every selector here is deliberately broad. What is NOT
# negotiable is that the confirm click lands on a button that belongs to OUR
# dialog: on the first attempt a stray 确定 elsewhere on the page was clicked,
# the dialog closed, and no order was ever created.
PANEL_SEL = ("[class*=modal],[class*=dialog],[role=dialog],[class*=buy-dialog],"
             "[class*=drawer],[class*=popup],[class*=mask],[class*=layer],"
             "[class*=panel]")
PANEL_SEL_JS = "'" + PANEL_SEL + "'"
DLG_TAG = "data-snap-dlg"       # tags the dialog we picked, so that the
                                 # region and the confirm hit the same box

# Words that mean "create this order" and nothing else. These are safe to
# click even when the dialog's class is unknown -- unlike 确定 / 确认, which
# are all over this page, and unlike 立即支付 / 去支付, which would PAY.
STRONG_CONFIRM = ("确认购买", "确认下单", "提交订单", "立即下单")

# The confirm button, by priority: 确认购买 before 确定.
# 立即支付 / 去支付 are NOT in here and never will be: paying is the user's
# job, always. (They used to be -- and the code disagreed with its own
# documentation. The rule wins.)
DIALOG_CONFIRM = ("确认购买", "确认下单", "提交订单", "立即下单",
                  "确定", "确认")

# Words that are only safe inside OUR panel -- the one the price gate has
# already verified -- and never on the page, where they belong to somebody
# else's card. 立即购买 is exactly that: on this page it is the label of the
# OTHER cards' buy buttons, but inside our own dialog it is our CTA.
PANEL_ONLY = ("立即购买",)


def _js(body):
    """Put the panel selector into a JS snippet."""
    return body.replace("@SEL@", PANEL_SEL_JS)


# Every panel on the page plus every visible button -- so the log says what
# the dialog looks like even when its class is one we do not know.
DUMP_JS = _js("""
() => {
    const sel = @SEL@;
    const panels = [];
    document.querySelectorAll(sel).forEach((p) => {
        const r = p.getBoundingClientRect();
        if (!r.width || !r.height) return;
        const t = (p.innerText || "").trim();
        if (!t || t.length > 1500) return;
        const btns = [...p.querySelectorAll("button,[role=button],[class*=btn],a")]
            .map((b) => (b.innerText || "").trim())
            .filter((x) => x && x.length < 20);
        panels.push({text: t.replace(/\\n+/g, " | ").slice(0, 400),
                     buttons: [...new Set(btns)],
                     w: Math.round(r.width), h: Math.round(r.height)});
    });
    const buttons = [];
    document.querySelectorAll("button,[role=button],[class*=btn],a").forEach((b) => {
        const r = b.getBoundingClientRect();
        const t = (b.innerText || "").trim();
        if (!r.width || !r.height || !t || t.length > 20) return;
        buttons.push({text: t, x: Math.round(r.x), y: Math.round(r.y)});
    });
    return {panels: panels.slice(0, 12), buttons: buttons.slice(0, 40)};
}
""")

# Find OUR dialog: a visible panel that names our product AND carries a
# confirm button. When several match, the smallest wins -- the dialog is a
# small box, while a full-screen mask that happens to contain the card is
# huge. The winner is tagged, so the region and the confirm act on the very
# same element.
#
# Two extra rules came out of a real lost order (2026-10-26 15:00):
#
#   * a panel must show OUR PRICE.  The spec alone is not enough: the page
#     has several 4核4G3M cards (109元, 99元, ...) and one of them is a row
#     of product cards that mentioned our spec and was mistaken for the buy
#     dialog -- the script then hunted 25 s for a confirm button in a panel
#     that never had one, and no order was placed.
#   * a panel that lists SEVERAL products is a product row, not a dialog.
#     A real buy dialog shows one price and one order button.
DIALOG_FIND_JS = _js("""
([sel, markers, words, tag, priceRe]) => {
    const PRICE = priceRe ? new RegExp(priceRe) : null;
    const countOf = (t, needle) => t.split(needle).length - 1;
    const label = (e) => (e.innerText || e.textContent || "").trim();
    const area = (e) => {
        const r = e.getBoundingClientRect();
        return r.width * r.height;
    };
    const viewport = window.innerWidth * window.innerHeight;
    // the words that mean "this box is about an order". 确定 and 确认 are
    // NOT among them: they are all over this page, and one of them sat in
    // the product card on 2026-10-26 -- clicking that is what lost the
    // first order.
    const ORDER_BTN = ["确认购买", "确认下单", "提交订单", "立即下单",
                       "立即支付", "去支付"];
    const ORDER_TEXT = ["订单"];
    // the loose pass: a panel may name its button 确定 or 确认. 立即购买 is
    // NOT in here -- that word is what every product card wears.
    const LOOSE_BTN = ORDER_BTN.concat(["确定", "确认"]);
    // the labels our own card's button wears. A box that carries one of
    // them is the CARD we just clicked, never the order panel.
    const CARD_STATE = ["立即抢购", "添加提醒", "取消提醒", "已抢光",
                        "已结束", "已抢完"];
    const acts = (p) => [...p.querySelectorAll(
        "button,[role=button],[class*=btn],a")]
        .map((b) => label(b))
        .filter((x) => x && x.length <= 14);
    const caches = {strict: new Map(), loose: new Map()};
    const qualifies = (p, loose) => {
        const cache = loose ? caches.loose : caches.strict;
        if (cache.has(p)) return cache.get(p);
        let ok = false;
        const r = p.getBoundingClientRect();
        // a full-screen overlay is never the dialog -- the page's own masks
        // hold the product card and sometimes a stray 确定, and clicking
        // that is exactly the mistake that lost the first order
        if (r.width && r.height && area(p) < viewport * 0.8) {
            const t = p.innerText || "";
            if (t && t.length <= 2000 &&
                markers.some((m) => t.includes(m))) {
                // our price, if we know it -- this is what makes the panel OURS
                if (!PRICE || PRICE.test(t)) {
                    const a = acts(p);
                    const buys = a.filter((x) => x === "立即购买").length;
                    // the card we clicked is not the dialog
                    const is_card = a.some((x) =>
                        CARD_STATE.some((w) => x === w || x.includes(w)));
                    // and a box only counts when it talks about an order
                    const orderish = loose
                        ? a.some((x) => LOOSE_BTN.some((w) => x === w || x.includes(w)))
                        : (a.some((x) => ORDER_BTN.some((w) => x === w || x.includes(w))) ||
                           ORDER_TEXT.some((w) => t.includes(w)));
                    // a row of products is not a dialog: it carries several
                    // buy buttons, and clicking one of them would order the
                    // wrong thing
                    ok = !is_card && buys <= 1 && orderish;
                }
            }
        }
        cache.set(p, ok);
        return ok;
    };
    const pool = [];
    const seen = new Set();
    const add = (p) => {
        if (!p || seen.has(p)) return;
        seen.add(p);
        if (qualifies(p, false)) pool.push(p);
    };
    // (1) the class names we know
    document.querySelectorAll(sel).forEach(add);
    // (2) the class names we do NOT know. This is the one that matters:
    // start from every button whose own text is an order word and walk UP
    // from it; the first box that also says our product is our dialog,
    // whatever its class is called.
    const ANCHOR = LOOSE_BTN.concat(["立即购买"]);
    const anchors = [];
    let n;
    const walker = document.createTreeWalker(
        document.body, NodeFilter.SHOW_TEXT);
    while ((n = walker.nextNode())) {
        const t = (n.nodeValue || "").trim();
        if (!t || t.length > 14) continue;
        if (!ANCHOR.some((w) => t === w)) continue;
        anchors.push(n.parentElement);
    }
    const climb = (loose) => {
        for (const start of anchors) {
            let p = start;
            for (let i = 0; i < 16 && p; i++) {
                if (qualifies(p, loose)) {
                    if (!seen.has(p)) {
                        seen.add(p);
                        pool.push(p);
                    }
                    break;
                }
                p = p.parentElement;
            }
        }
    };
    climb(false);
    if (!pool.length) {
        // (3) the loose pass: the panel may name its button 确定, and then
        // only the price gate and the card rule keep it apart from the
        // rest of the page
        climb(true);
    }
    document.querySelectorAll("[" + tag + "]").forEach((e) => e.removeAttribute(tag));
    if (!pool.length) return null;
    pool.sort((a, b) => area(a) - area(b));
    const p = pool[0];
    p.setAttribute(tag, "1");
    const r = p.getBoundingClientRect();
    const btns = acts(p).filter((x) => x.length < 20);
    return {text: (p.innerText || "").replace(/\\n+/g, " | ").slice(0, 300),
            buttons: [...new Set(btns)],
            w: Math.round(r.width), h: Math.round(r.height)};
}
""")

# What the page actually has. When the dialog is not found, this says which
# boxes mention our price and what their class is called -- the one thing
# the old report could never tell us. On 2026-10-27 the click was perfect
# and the server was asked to open the order, but nothing said what the
# order panel looked like, so we could not tell a missed dialog from a
# missing one.
PRICE_SCAN_JS = """
([needle]) => {
    const out = [];
    const seen = new Set();
    const walker = document.createTreeWalker(
        document.body, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
        if ((n.nodeValue || "").indexOf(needle) < 0) continue;
        let p = n.parentElement;
        for (let i = 0; i < 6 && p; i++) {
            const r = p.getBoundingClientRect();
            const cls = (typeof p.className === "string") ? p.className : "";
            const key = p.tagName + "|" + cls + "|" +
                        Math.round(r.width) + "x" + Math.round(r.height);
            if (!seen.has(key) && r.width && r.height) {
                seen.add(key);
                const btns = [...p.querySelectorAll(
                    "button,[role=button],[class*=btn],a")]
                    .map((b) => (b.innerText || "").trim())
                    .filter((x) => x && x.length < 20);
                out.push({tag: p.tagName, cls: cls.slice(0, 70),
                          w: Math.round(r.width), h: Math.round(r.height),
                          text: (p.innerText || "").replace(/\\n+/g, " | ")
                                .slice(0, 160),
                          buttons: [...new Set(btns)].slice(0, 8)});
            }
            p = p.parentElement;
        }
        if (out.length > 24) break;
    }
    return out;
}
"""

# The region, if the dialog asks for one. Its own step, because picking a
# region re-renders the dialog and would leave the confirm button stale.
DIALOG_REGION_JS = """
([tag, regions]) => {
    const p = document.querySelector("[" + tag + "='1']");
    if (!p) return null;
    if (!(p.innerText || "").includes("地域")) return null;
    for (const n of regions) {
        const el = [...p.querySelectorAll(
            "button,[role=button],[class*=tab],[class*=item],[class*=radio],"
            + "label,li,[class*=card-item],[class*=option],[class*=cell]")]
            .find((e) => (e.innerText || "").trim() === n);
        if (el) { el.click(); return n; }
    }
    return null;
}
"""

# The confirm click, in three tiers:
#   1. inside the dialog we tagged -- DIALOG_CONFIRM, by priority
#   2. inside that same dialog -- PANEL_ONLY (立即购买), but only when it is
#      the one and only such button in there; with two of them we do not
#      know which one is ours, so we refuse and let the report say so
#   3. the dialog's class is unknown -- an order word is unambiguous, but
#      only when exactly one such button is on the page
DIALOG_CONFIRM_JS = """
([tag, words, strong, inside]) => {
    const inView = (e) => {
        const r = e.getBoundingClientRect();
        return r.width && r.height &&
               r.bottom > 0 && r.top < window.innerHeight;
    };
    const label = (e) => (e.innerText || "").trim();
    const all = (root) => [...root.querySelectorAll(
        "button,[role=button],[class*=btn],a")];
    const p = document.querySelector("[" + tag + "='1']");
    if (p) {
        for (const w of words) {
            const b = all(p).find((e) => {
                const bt = label(e);
                return bt && bt.length <= 14 && inView(e) &&
                       (bt === w || bt.includes(w));
            });
            if (b) {
                const t = label(b);
                b.click();
                return {text: t, where: "our dialog"};
            }
        }
        for (const w of (inside || [])) {
            const hits = all(p).filter((e) => {
                const bt = label(e);
                return bt && bt.length <= 14 && inView(e) &&
                       (bt === w || bt.includes(w));
            });
            if (hits.length === 1) {
                const t = label(hits[0]);
                hits[0].click();
                return {text: t, where: "our dialog (the panel's own button)"};
            }
            if (hits.length > 1) {
                return {text: null, where: "our dialog",
                        ambiguous: hits.map((h) => label(h))};
            }
        }
    }
    for (const w of strong) {
        const hits = [...document.querySelectorAll(
            "button,[role=button],[class*=btn],a")]
            .filter((e) => {
                const bt = label(e);
                return bt && bt.length <= 14 && inView(e) &&
                       (bt === w || bt.includes(w));
            });
        if (hits.length === 1) {
            const t = label(hits[0]);
            hits[0].click();
            return {text: t, where: "the page (unique order button)"};
        }
    }
    return null;
}
"""


def handle_purchase(context, page, seconds=25, drain=None, stop=None,
                    again_at=None, clicker=None, not_before=None):
    """Wait for the buy dialog and confirm it -- in whichever tab it opened.

    The confirm click must land on OUR dialog: on the first attempt a stray
    确定 elsewhere on the page was clicked, the dialog closed, and no order
    was ever created. So the panel has to name our product and carry a
    confirm button, and the region is chosen inside that same panel.

    drain  -- called every round: it reads the server's answers, which is
              the only way to learn that the server refused the order
    stop   -- returns a reason string to give up at once. The server's
              refusal means no panel will ever open, so there is no point
              in hunting for one.
    again_at -- a moment worth clicking again (the opening of the sale):
              if the first click went out early and was refused, the
              refusal can take seconds to come back -- by then the sale
              is open and nobody is clicking. So the click is repeated.
    clicker  -- how to click again (a JS click: a real mouse click would
              land on the mask of an early refusal and could close the
              panel we just opened).
    not_before -- do not confirm before this moment: the sale has to be
              open, or the order request is refused.

    Returns a verdict dict, so the run can say WHY it ended:

        {"ok": True/False, "code": "FAIL_...", "detail": "...",
         "panels": [...], "buttons": [...]}
    """
    end = time.time() + seconds
    dumped = set()
    seen_panels = []          # every panel we laid eyes on, for the report
    seen_buttons = []
    our_panel = None
    region = None
    while time.time() < end:
        if drain:
            try:
                drain()
            except Exception:
                pass
        if stop:
            try:
                why = stop()
            except Exception:
                why = None
            if why:
                # the caller may hand back (code, reason) or just a reason
                if isinstance(why, (list, tuple)) and len(why) == 2:
                    code, reason = why
                else:
                    code, reason = "FAIL_PREPARE_REJECTED", why
                note("DIALOG", "giving up early: %s" % reason)
                return {"ok": False, "code": code,
                        "detail": reason, "panels": seen_panels,
                        "buttons": seen_buttons}
        # The sale is open now and our panel has not shown up: the early
        # click was refused. Click again -- now the server will answer.
        if again_at and our_panel is None:
            if time.time() >= again_at:
                again_at = None
                if clicker:
                    try:
                        if clicker():
                            note("FIRE", "the session is open now -- "
                                         "clicking again")
                    except Exception:
                        pass
            else:
                # sleep right up to that moment: the second click must
                # not be late, and the hunt polls only every 250 ms
                time.sleep(min(0.05, again_at - time.time()))
                continue
        for pg in list(context.pages):
            frames = [pg.main_frame] + [f for f in pg.frames if f != pg.main_frame]
            for fr in frames:
                try:
                    dump = fr.evaluate(DUMP_JS)
                except Exception:
                    continue
                for d in dump.get("panels") or []:
                    key = "P " + d["text"][:60]
                    if key in dumped:
                        continue
                    dumped.add(key)
                    seen_panels.append(d)
                    note("DIALOG", "panel %dx%d  %s"
                         % (d["w"], d["h"], d["text"][:260]))
                    note("DIALOG", "  buttons=%s" % d["buttons"])
                buttons = dump.get("buttons") or []
                key = "B " + ",".join(b["text"] for b in buttons)[:80]
                if buttons and key not in dumped:
                    dumped.add(key)
                    seen_buttons = [b["text"] for b in buttons]
                    note("DIALOG", "page buttons=%s"
                         % ", ".join("%s@%d,%d" % (b["text"], b["x"], b["y"])
                                     for b in buttons[:20]))
                try:
                    found = fr.evaluate(DIALOG_FIND_JS,
                                        [PANEL_SEL, list(BUY_MARKERS),
                                         list(CONFIRM_WORDS), DLG_TAG,
                                         PRICE_RE])
                except Exception:
                    found = None
                if found:
                    fresh = our_panel is None
                    our_panel = found
                    if fresh:
                        note("DIALOG", "our dialog %dx%d  %s"
                             % (found["w"], found["h"], found["text"][:260]))
                        note("DIALOG", "  buttons=%s" % found["buttons"])
                        try:
                            region = fr.evaluate(DIALOG_REGION_JS,
                                                 [DLG_TAG, list(REGION_WORDS)])
                        except Exception:
                            region = None
                        if region:
                            note("DIALOG", "picked region %r" % region)
                            try:
                                fr.wait_for_timeout(700)
                            except Exception:
                                pass
                # The panel is open, but the sale may not be: confirming
                # before the session starts gets the order refused, and a
                # refused order is not retried by the page.
                if (not_before and our_panel is not None
                        and time.time() < not_before):
                    left = not_before - time.time()
                    if left > 0.05:
                        note("DIALOG", "the panel is open but the session opens "
                             "in %.1f s -- waiting for it" % left)
                        time.sleep(min(0.25, left))
                        continue
                try:
                    got = fr.evaluate(DIALOG_CONFIRM_JS,
                                      [DLG_TAG, list(DIALOG_CONFIRM),
                                       list(STRONG_CONFIRM), list(PANEL_ONLY)])
                except Exception:
                    got = None
                if got and got.get("ambiguous"):
                    note("DIALOG", "several buttons say %r -- not touching them"
                         % got["ambiguous"])
                    return {"ok": False, "code": "FAIL_NO_CONFIRM",
                            "detail": "our panel has %d buttons labelled %r"
                                      % (len(got["ambiguous"]), got["ambiguous"][0]),
                            "panels": seen_panels, "buttons": seen_buttons}
                if got:
                    note("DIALOG", "clicked %r in %s" % (got["text"], got["where"]))
                    try:
                        note("DIALOG", "url=%s" % fr.page.url)
                        fr.wait_for_timeout(1800)
                    except Exception:
                        pass
                    return {"ok": True, "code": "OK_DIALOG_CONFIRMED",
                            "detail": "clicked %r in %s" % (got["text"],
                                                            got["where"]),
                            "region": region,
                            "panels": seen_panels, "buttons": seen_buttons}
        page.wait_for_timeout(250)

    # -- out of time: say what we saw, so the next run knows more ----------
    # Every box on the page that mentions our price, with its class name.
    # This is the answer to "what did the order panel look like?", which
    # the old report could not give.
    boxes = []
    try:
        boxes = page.evaluate(PRICE_SCAN_JS, [PRICE_TEXT]) or []
    except Exception:
        boxes = []
    if boxes:
        note("DIALOG", "%d box(es) mention %s:" % (len(boxes), PRICE_TEXT))
        for b in boxes[:8]:
            note("DIALOG", "  <%s class=%r> %dx%d buttons=%s  %s"
                 % (b["tag"], b["cls"], b["w"], b["h"], b["buttons"],
                    b["text"][:100]))
    if our_panel:
        code = "FAIL_NO_CONFIRM"
        detail = ("our panel was found (%dx%d) but it carries no button we are "
                  "willing to click: %s"
                  % (our_panel["w"], our_panel["h"], our_panel["buttons"]))
    elif seen_panels:
        code = "FAIL_DIALOG_WRONG"
        detail = ("no panel of ours appeared; %d other panels were on the "
                  "page, e.g. %s"
                  % (len(seen_panels), seen_panels[0]["text"][:160]))
    else:
        code = "FAIL_NO_DIALOG"
        detail = "no panel of any kind appeared after the click"
    if boxes:
        detail += ("; %d box(es) on the page mention %s (see the timeline)"
                   % (len(boxes), PRICE_TEXT))
    note("DIALOG", "giving up: %s" % detail)
    return {"ok": False, "code": code, "detail": detail,
            "panels": seen_panels, "buttons": seen_buttons, "boxes": boxes}


def is_too_early(reply):
    """Did the server refuse because the sale had not opened yet?

    1101001 is the code the flash-sale API answers with before the session
    starts, and the message says the same in words ("not started" /
    "秒杀尚未开始"). Such a click is wasted, not wrong: the order goes
    through on the next try, one second later.
    """
    if not reply:
        return False
    if str(reply.get("code")) == "1101001":
        return True
    msg = "%s %s" % (reply.get("msg") or "", reply.get("text") or "")
    return ("not started" in msg.lower() or "尚未开始" in msg
            or "未开始" in msg)


def is_refused(reply):
    """Did the server refuse to open the order?

    A refusal before the session opens ("秒杀尚未开始", code 1101001) is
    not a failure: the click was simply early, and the order goes through
    on the next try. A refusal after it opened is the real thing.
    """
    if not reply:
        return False
    code = reply.get("code")
    if code is None:
        return False
    return str(code) not in ("0", "200")


def reply_for(order_state, n):
    """The server's answer to the n-th click (1-based), or None.

    Every click makes the page send one "prepare-do" request, and the
    answers come back in the same order. So the n-th answer belongs to
    the n-th click -- the only way to tell a stale refusal (from the
    early click) from the answer to the click that is in flight now.
    """
    replies = order_state.get("prepare_replies") or []
    if n and len(replies) >= n:
        return replies[n - 1]
    return None


def last_click_at(order_state):
    """When did the most recent click go out?"""
    ats = order_state.get("click_ats") or []
    return ats[-1] if ats else None


# What the run does after it has hunted for the buy panel.
MOVE_SOLD_OUT = "sold_out"      # the goods are gone; wait for the next session
MOVE_REFUSED = "refused"        # the server refused, and the sale was open
MOVE_RETRY_AT_OPEN = "retry"    # the click was early: go again at the opening
MOVE_HUNT = "hunt"              # no answer yet: keep looking for the panel
MOVE_CLICK = "click"            # nothing wrong: click again


def next_move(rep, last_at, open_at):
    """What to do after a hunt for the buy panel.

    rep     -- the server's answer to the newest click, or None
    last_at -- when that click went out
    open_at -- when the sale opens (None when the page did not say)

    The rules, in order:

      * the goods are gone -- no amount of clicking will bring them back
      * the answer is a refusal and the click went out BEFORE the sale
        opened: the sale was not open yet, so this is not a lost order.
        Go again the moment it opens.
      * the answer is a refusal and the click went out after the sale
        opened: that is that
      * no answer at all: the request may still be on its way -- keep
        looking for the panel
      * anything else: click again
    """
    if rep and is_sold_out(rep):
        return MOVE_SOLD_OUT
    went_early = bool(open_at and last_at and last_at < open_at)
    if rep and is_refused(rep):
        return MOVE_RETRY_AT_OPEN if went_early else MOVE_REFUSED
    if rep is None:
        return MOVE_HUNT
    return MOVE_CLICK


def is_sold_out(reply):
    """Did the server say the goods are gone?

    1100136 is the code the flash-sale API answers with when the item is
    sold out ("抱歉，当前商品已售罄"), and the reply carries
    "available": 0. There is nothing to wait for: the next session has to
    come, and no amount of clicking will change the answer.
    """
    if not reply:
        return False
    if str(reply.get("code")) == "1100136":
        return True
    msg = "%s %s" % (reply.get("msg") or "", reply.get("text") or "")
    return ("售罄" in msg or "sold out" in msg.lower() or "sold out" in msg)


def hold(page):
    """Stop touching the page -- the user takes over. Log only."""
    note("HOLD", "the page is yours now; click 立即抢购 and the confirm button "
                  "yourself, then pay in this window")
    note("HOLD", "I keep logging the requests so we can see the order go through")
    while True:
        try:
            page.wait_for_timeout(1000)
        except Exception:
            # the browser was closed -- that is not a crash, and it must
            # not overwrite the verdict the run already earned
            note("HOLD", "the browser was closed")
            return


# --------------------------------------------------------------------------
# the live run
# --------------------------------------------------------------------------
def main():
    """Run the buyer, and always leave a report behind.

    A run that dies on an exception must still answer the only question
    that matters: why did it stop? So the report is written in every
    case -- success, a known failure, or a crash.
    """
    report = snap_report.get()
    try:
        _run()
    except Exception as exc:
        import traceback
        note("FATAL", "%s: %s" % (type(exc).__name__, exc))
        report.fact("traceback", traceback.format_exc()[-2000:])
        report.fail("FAIL_UNKNOWN",
                    "脚本自己崩了：%s: %s" % (type(exc).__name__, exc))
        raise
    finally:
        report.write()


def _run():
    global logf
    logf = open(LOG, "w", encoding="utf-8", buffering=1)
    survey_only = "--survey" in sys.argv
    report = snap_report.get()

    # how long we are willing to sit and wait for the session to open. A run
    # always ends with a verdict, so it cannot wait forever.
    max_wait_min = 240.0
    for arg in sys.argv[1:]:
        if arg.startswith("--max-wait"):
            val = arg.split("=", 1)[1] if "=" in arg else None
            if val:
                max_wait_min = float(val)
    report.fact("max wait (minutes)", max_wait_min)

    cookies = json.load(open("cookies.json", encoding="utf-8"))
    note("START", "%s  cookies: %d" % (BUILD, len(cookies)))
    report.fact("build", BUILD)
    report.fact("cookies", len(cookies))
    cfg = json.load(open("config.json", encoding="utf-8"))
    act_id = cfg.get("server_act_id")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        context.add_cookies(cookies)

        seen = set()
        # The answers of the order endpoints are what the report needs. The
        # body of a response cannot be read inside the event handler (it
        # would re-enter the sync API), so the responses are collected here
        # and read later, outside the handler.
        pending_bodies = []
        order_state = {"order_request": False, "order_response": None,
                       "prepare_replies": [], "clicks": 0, "click_ats": []}

        def drain_bodies():
            """Read the bodies we collected. Safe to call any time."""
            while pending_bodies:
                resp = pending_bodies.pop(0)
                u = urlsplit(resp.url)
                try:
                    body = (resp.text() or "").strip()
                except Exception as exc:
                    body = "<body unavailable: %s>" % exc
                body = body[:500]
                note("RESP", "%s %s%s  %s" % (resp.status, u.netloc, u.path,
                                              body or "<empty>"))
                report.fact("response %s" % u.path.rsplit("/", 1)[-1],
                            "HTTP %s %s" % (resp.status, body[:200]))
                if "do-goods" in u.path:
                    order_state["order_response"] = (resp.status, body)
                if "prepare-do" in u.path:
                    # The server's answer to "open the order". On 2026-10-27
                    # this was never read, so the run could not say whether
                    # the order was refused because the sale had not opened
                    # yet or because it was gone.
                    code = None
                    msg = ""
                    try:
                        j = json.loads(body)
                        code = j.get("code")
                        msg = j.get("msg") or j.get("message") or ""
                    except Exception:
                        pass
                    order_state["prepare_replies"].append({
                        "status": resp.status, "code": code, "msg": msg,
                        "text": body[:300], "at": time.time()})
                    note("FIRE", "the server answered the order request: %s"
                         % (msg or body[:120]))

        def on_request(req):
            u = urlsplit(req.url)
            if not HOST_RE.search(u.netloc):
                return
            if any(k in u.path for k in ("do-goods", "to-settle")):
                order_state["order_request"] = True
                note("ORDER", "the page is placing the order: %s %s"
                     % (req.method, u.path))
            key = (req.method, u.netloc, u.path)
            if key not in seen:
                seen.add(key)
                note("REQ", "%s %s%s" % (req.method, u.netloc, u.path))
            raw = req.post_data
            if not raw:
                return
            try:
                data = json.loads(raw)
            except ValueError:
                return
            # While the page loads it fires one check-available / query-price
            # pair PER CARD on the page -- a couple of hundred requests whose
            # bodies are all identical schema dumps. Logging them buried the
            # lines that matter, so only the flash-sale traffic is recorded.
            if not any(k in u.path for k in INTEREST):
                return

            def shape(node, depth=0):
                if depth > 2:
                    return "..."
                if isinstance(node, dict):
                    return "{" + ", ".join(
                        "%s=%s" % (k, shape(v, depth + 1))
                        for k, v in list(node.items())[:10]) + "}"
                if isinstance(node, list):
                    if not node:
                        return "[]"
                    return "[%d x %s]" % (len(node), shape(node[0], depth + 1))
                if isinstance(node, str):
                    return '"%s"' % node[:20]
                return str(node)

            note("BODY", "%s %s %s" % (req.method, u.path, shape(data)[:300]))

        def on_response(resp):
            u = urlsplit(resp.url)
            if not HOST_RE.search(u.netloc):
                return
            if any(k in u.path for k in ("do-goods", "do-voucher", "subs-seckill",
                                         "check-available", "to-settle", "add-cart")):
                note("RES", "%s %s%s" % (resp.status, u.netloc, u.path))
            if any(k in u.path for k in ("prepare-do", "do-goods", "to-settle",
                                         "add-cart", "do-voucher")):
                pending_bodies.append(resp)

        context.on("request", on_request)
        context.on("response", on_response)

        page = context.new_page()
        page.on("dialog", lambda d: d.accept())
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        note("NAV", page.url)

        # ---- the schedule ------------------------------------------------
        sessions = session_schedule(page)
        note("SCHED", "%d session(s) on the page" % len(sessions))
        for s in sessions:
            note("SCHED", "  %s %s-%s  %s" % (s["date"], s["start"], s["end"],
                                              s["status_cn"]))
        target = describe(sessions, act_id)
        if not target:
            note("FATAL", "no session for act_id %s" % act_id)
            report.fail("FAIL_NO_CARD", "no session on the page for act_id %s"
                        % act_id)
        else:
            note("SCHED", "our session: %s %s-%s %s"
                 % (target["date"], target["start"], target["end"],
                    target["status_cn"]))
            report.fact("session", "%s %s-%s (%s)"
                        % (target["date"], target["start"], target["end"],
                           target["status_cn"]))
            report.fact("act_id", act_id)

        # the next session that still has a chance
        upcoming = []
        for s in sessions:
            if not ours(s, act_id) or s.get("status") == 2:
                continue
            st = session_start(s)
            if st:
                upcoming.append((st, s))
        upcoming.sort()
        for st, s in upcoming:
            mins = (st - datetime.now(timezone.utc)).total_seconds() / 60.0
            when = ("opens in %.1f min" % mins) if mins > 0 else "already open"
            note("SCHED", "  next: %s %s-%s %s (%s)"
                 % (s["date"], s["start"], s["end"], s["status_cn"], when))

        # When our session opens, as a local timestamp. The button flips a
        # moment BEFORE that -- it is armed, not open -- and a click that
        # lands too early is thrown away by the server ("not started"), so
        # the click waits for the opening when we know the time.
        open_at = next_opening(upcoming, time.time())
        if open_at:
            report.fact("session opens at",
                        time.strftime("%H:%M:%S", time.localtime(open_at)))
            note("SCHED", "our session opens at %s (local time)"
                 % time.strftime("%H:%M:%S", time.localtime(open_at)))

        # ---- survey the product cards -----------------------------------
        priced = []
        for c in survey(page):
            hit = PRICE_TEXT if c.get("has38") else "    "
            note("CARD", "%s btn=%-8r score=%-2d quota=%-5s [%s] | %s"
                 % (hit, c["text"], score(c), c.get("hasMarker"),
                    (c.get("title") or "")[:18], c["card"][:110]))
            if c.get("has38"):
                priced.append(c)
        if priced:
            note("CARD", "==> the %s card's button says %r"
                 % (PRICE_TEXT, priced[0]["text"]))
        else:
            note("CARD", "==> no %s card in this pass" % PRICE_TEXT)

        if survey_only:
            note("DONE", "survey only -- nothing was clicked")
            report.set("OK_SURVEY_ONLY", "只看不点，没有下单")
            report.write()
            browser.close()
            return

        best = find_target(page)
        if not best:
            note("FATAL", "could not find the %s / %s card"
                 % (SPEC_TEXT, PRICE_TEXT))
            note("HOLD", "scroll to 限时秒杀 yourself and click 立即抢购")
            report.fail("FAIL_NO_CARD",
                        "页面上没扫到 %s / %s 的卡（扫到 %d 张候选）"
                        % (SPEC_TEXT, PRICE_TEXT, len(cands))
                        % len(priced))
            report.shot(page, "fail-no-card.png")
            report.write()
            hold(page)
            return

        note("TARGET", "button %r  top=%d  card=%s"
             % (best["text"], best["top"], best["card"][:200]))
        mark(page, best["idx"])
        reveal(page, best["idx"])
        note("TARGET", "outlined; it should be in the middle of the window")

        # ---- wait for the session to open, then click --------------------
        # The page polls its own seckill state every ~13 s, so the button
        # flips by itself: no reload, no blind window, and the page stays
        # exactly where the user left it (which is what makes it clickable).
        note("WAIT", "waiting for the button to say 立即抢购")
        attempts = 0
        bought = False
        last_text = None
        sold = None
        flip_at = None          # when the button turned into 立即抢购
        click_at = None         # when the click actually landed
        last_verdict = None
        early_waited = False    # did we hold the click back for the opening?
        holding = False         # are we holding the click right now?
        need_click = True       # is the next thing to do a click?
        hunts = 0               # how many times we looked for the panel
        give_up_at = time.time() + max_wait_min * 60

        def second_bite():
            """The second bite: click the button again the moment the sale
            opens, in case the early click was refused for being early.

            It goes straight to the button's own handler (a real mouse
            click would land on the mask of the refusal and could close
            the panel that just opened).
            """
            order_state["clicks"] += 1
            order_state["click_ats"].append(time.time())
            return click_target_js(page)

        def prepare_stop():
            """Has the server refused to open the order? Then no panel is
            coming, and waiting for one only wastes the sale.

            Only the answer to the *latest* click counts. A stale refusal
            from the early click must not end the hunt while the second
            click is still on its way.
            """
            rep = reply_for(order_state, order_state.get("clicks"))
            if rep:
                code = rep.get("code")
                if is_sold_out(rep):
                    return ("FAIL_SOLD_OUT",
                            "the server says this item is sold out: code=%s "
                            "msg=%s" % (code, rep.get("msg")
                                       or rep.get("text") or "?"))
                if code not in (None, 0, 200, "0", "200"):
                    return ("FAIL_PREPARE_REJECTED",
                            "the server refused to open the order: code=%s "
                            "msg=%s" % (code, rep.get("msg")
                                       or rep.get("text") or "?"))
            # and when the card itself says the sale is over, there is
            # nothing left to wait for
            try:
                t = button_text(page)
            except Exception:
                t = None
            if t in ("已抢光", "已结束", "已抢完"):
                return ("FAIL_SOLD_OUT",
                        "the card says %r -- this session is over" % t)
            return None

        while True:
            if time.time() > give_up_at:
                note("FATAL", "waited %.0f min and the button never turned "
                              "into 立即抢购" % max_wait_min)
                report.fail("FAIL_NO_FLIP",
                            "等了 %.0f 分钟，按钮一直没变成「立即抢购」"
                            % max_wait_min)
                report.shot(page, "fail-no-flip.png")
                report.write()
                hold(page)
                return
            try:
                txt = button_text(page)
            except Exception:
                txt = None
            if txt is None:
                if page.is_closed():
                    note("FATAL", "the browser was closed")
                    report.fail("FAIL_UNKNOWN",
                                "浏览器被关掉了，脚本没法继续了")
                    report.write()
                    return
                # the marked element is gone (scrolled out of the DOM, or
                # React replaced the node) -- find and mark it again
                c = remark(page) or find_target(page, budget=12.0)
                if c:
                    mark(page, c["idx"])
                    note("WAIT", "re-marked on %r" % c["text"])
                    page.wait_for_timeout(200)
                else:
                    note("WAIT", "card not found -- reloading the page")
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(3000)
                    except Exception as exc:
                        note("FATAL", "the page could not be reloaded: %s"
                             % str(exc)[:160])
                        report.fail("FAIL_UNKNOWN",
                                    "页面刷新失败：%s" % str(exc)[:120])
                        report.write()
                        return
                    c = find_target(page, budget=12.0)
                    if c:
                        mark(page, c["idx"])
                        reveal(page, c["idx"])
                continue

            if txt != last_text:
                note("BTN", "target button = %r" % txt)
                last_text = txt

            if is_buy_text(txt):
                if flip_at is None:
                    flip_at = time.time()
                    report.fact("button flipped at",
                                time.strftime("%H:%M:%S", time.localtime(flip_at)))
                    note("FIRE", "the button flipped -- timing the click")
                # The button is armed a moment before the sale opens. A
                # click that lands too early comes back as "not started"
                # and the order is gone, so hold it -- but release it a
                # moment BEFORE the opening: on 2026-10-28 the click went
                # out exactly on time and the server answered "sold out"
                # three seconds later, so being first in the queue is
                # worth more than being exactly on time.
                left = (open_at - time.time()) if open_at else -1
                if not holding and should_hold(flip_at, open_at):
                    holding = True
                    early_waited = True
                    note("FIRE", "the button is ready but the session "
                                 "opens in %.1f s -- waiting for it" % left)
                    report.fact("button ready before the session",
                                "%.1f s early" % left)
                if holding and release_in(open_at) > 0:
                    page.wait_for_timeout(
                        min(int(release_in(open_at) * 1000), 2000))
                    continue
                if need_click:
                    attempts += 1
                    note("FIRE", "attempt %d: %r -- clicking" % (attempts, txt))
                    if not click_target(page):
                        note("FIRE", "click did not land")
                        page.wait_for_timeout(200)
                        continue
                    click_at = time.time()
                    order_state["clicks"] += 1
                    order_state["click_ats"].append(click_at)
                    report.fact("click landed at",
                                time.strftime("%H:%M:%S",
                                              time.localtime(click_at)))
                    report.fact("flip->click (ms)",
                                int((click_at - flip_at) * 1000))
                    note("FIRE", "clicked; hunting for the buy dialog")
                    need_click = False
                # Hunt for the buy panel. This happens right after a click,
                # and again whenever the server has not answered yet.
                #
                # The fallback click at the opening only makes sense while
                # the newest click went out BEFORE the opening: once the
                # click at the opening has happened, a second one could
                # open a second order.
                again = None
                if open_at:
                    newest = last_click_at(order_state)
                    if newest is None or newest < open_at - 0.2:
                        again = open_at
                hunts += 1
                result = handle_purchase(context, page, seconds=25,
                                         drain=drain_bodies, stop=prepare_stop,
                                         again_at=again,
                                         clicker=second_bite,
                                         not_before=open_at)
                last_verdict = result
                if result.get("ok"):
                    bought = True
                    break
                report.fact("dialog verdict", result.get("code"))
                report.fact("dialog detail", result.get("detail"))
                if result.get("buttons"):
                    report.fact("page buttons at that moment",
                                ", ".join(result["buttons"][:25]))
                for b in (result.get("boxes") or [])[:6]:
                    report.fact("box mentioning %s" % PRICE_TEXT,
                                "<%s class=%r> %dx%d buttons=%s | %s"
                                % (b["tag"], b["cls"], b["w"], b["h"],
                                   b["buttons"], b["text"][:80]))
                # What did the server say to the click that is the newest
                # one? Every click makes one request and the answers come
                # back in order, so the n-th answer is the n-th click's.
                rep = reply_for(order_state, order_state.get("clicks"))
                last_at = last_click_at(order_state)
                if rep:
                    report.fact("the server's answer",
                                "HTTP %s code=%s msg=%s"
                                % (rep.get("status"), rep.get("code"),
                                   rep.get("msg") or rep.get("text") or ""))
                # write the report now, not at the end: a run that is
                # killed (the window closed, the terminal closed) must
                # still leave its evidence behind. On 2026-10-28 the
                # process was killed after the sale was lost and the
                # folder was empty.
                report.write()
                move = next_move(rep, last_at, open_at)
                # The goods are gone. No amount of clicking will bring
                # them back -- the next session has to come.
                if move == MOVE_SOLD_OUT:
                    note("FIRE", "the server says the item is sold out -- "
                                 "this session is over")
                    report.fail("FAIL_SOLD_OUT",
                                "服务器说「当前商品已售罄」：这一场的货在开卖后 "
                                "几秒内就没了，脚本点到的时候已经没了。"
                                "下一场要重新起脚本。")
                    report.write()
                    hold(page)
                    return
                # The limit only counts once the sale is open: the early
                # clicks are a scramble, not five lost attempts.
                if attempts >= 5 and (not open_at or time.time() >= open_at):
                    note("FIRE", "no order after %d attempts -- the page is yours"
                         % attempts)
                    report.fail(result.get("code") or "FAIL_NO_CONFIRM",
                                "试了 %d 次都没成：%s"
                                % (attempts, result.get("detail")))
                    report.shot(page, "fail-after-%d-attempts.png" % attempts)
                    report.write()
                    hold(page)
                    return
                # The card says the session is over: clicking again is
                # pointless -- the goods are gone.
                try:
                    now_txt = button_text(page)
                except Exception:
                    now_txt = None
                if now_txt in ("已抢光", "已结束", "已抢完"):
                    note("FIRE", "the card says %r -- this session is over"
                         % now_txt)
                    report.fail("FAIL_SOLD_OUT",
                                "卡片变成「%s」：这一场卖完了。" % now_txt)
                    report.write()
                    hold(page)
                    return
                # The click went out before the sale opened: whatever the
                # server said, the sale was not open yet -- go again the
                # moment it opens. That is not a lost order.
                if move == MOVE_RETRY_AT_OPEN:
                    wait = max(0.0, open_at - time.time())
                    why = ("the sale had not started yet" if is_too_early(rep)
                           else "the click was too early")
                    note("FIRE", "%s -- the sale opens in %.1f s, going again "
                                 "then" % (why, wait))
                    report.fact("the click was refused",
                                "%s; the click went out %.1f s before the "
                                "opening" % (why, open_at - last_at))
                    need_click = True
                    # wait right up to the opening: clicking before it
                    # would only be refused again
                    page.wait_for_timeout(min(int(wait * 1000), 8000))
                    continue
                if move == MOVE_REFUSED:
                    note("FIRE", "the server refused the order and the sale is "
                                 "open -- that is that")
                    report.fail("FAIL_PREPARE_REJECTED",
                                "接口拒绝了这次下单：code=%s msg=%s"
                                % (rep.get("code"),
                                   rep.get("msg") or rep.get("text") or ""))
                    report.write()
                    hold(page)
                    return
                if move == MOVE_HUNT:
                    # The server has not answered the newest click yet:
                    # the order may still be on its way. Keep looking.
                    if hunts >= 3:
                        note("FIRE", "the server never answered -- the page "
                                     "is yours")
                        report.fail("FAIL_PREPARE_REJECTED",
                                    "点了 %d 次，服务器始终没有回答这次下单请求。"
                                    % attempts)
                        report.shot(page, "fail-no-answer.png")
                        report.write()
                        hold(page)
                        return
                    need_click = False
                    note("FIRE", "no answer yet -- waiting for the panel")
                    page.wait_for_timeout(200)
                    continue
                need_click = True
                note("FIRE", "no order yet -- clicking again")
                page.wait_for_timeout(500)
                continue

            if txt in ("已抢光", "已结束", "已抢完") and sold != txt:
                sold = txt
                note("STATE", "the %s card says %r -- sold out in this session"
                     % (PRICE_TEXT, txt))
                report.fact("button said", txt)
            elif txt not in ("已抢光", "已结束", "已抢完"):
                sold = None

            page.wait_for_timeout(80)

        note("FIRE", "confirm clicked -- finish paying in this window")
        page.wait_for_timeout(3000)
        note("STATE", "url = %s" % page.url)
        note("NEXT", "browser stays open -- pay in this window")
        drain_bodies()
        if order_state["order_request"]:
            report.ok("点了确认，页面已经发出下单请求")
        else:
            # we confirmed, but the page never placed an order
            report.fail("FAIL_NO_ORDER_REQ",
                        "点了确认，但页面没有发出任何下单请求（do-goods）")
            report.shot(page, "fail-no-order-request.png")
        report.write()

        try:
            while True:
                page.wait_for_timeout(2000)
        except KeyboardInterrupt:
            pass
        finally:
            if logf:
                logf.close()
            browser.close()


if __name__ == "__main__":
    main()
