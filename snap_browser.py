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
BUILD = ("b5: the buy panel must show our price, product rows are not "
         "dialogs, 立即支付 is never clicked, and every run writes a report")

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
    const ok = (bt) => {
        bt = (bt || "").trim();
        return bt && bt.length <= 14 &&
               words.some((w) => bt === w || bt.includes(w));
    };
    const PRICE = priceRe ? new RegExp(priceRe) : null;
    const countOf = (t, needle) => t.split(needle).length - 1;
    const panels = [...document.querySelectorAll(sel)].filter((p) => {
        const t = p.innerText || "";
        const r = p.getBoundingClientRect();
        if (!(r.width && r.height)) return false;
        if (!markers.some((m) => t.includes(m))) return false;
        // our price, if we know it -- this is what makes the panel OURS
        if (PRICE && !PRICE.test(t)) return false;
        // a row of products is not a dialog: it quotes several prices and
        // carries several buy buttons, and clicking one of them would order
        // the wrong thing
        const prices = countOf(t, "活动价") + countOf(t, "日常价");
        const buys = [...p.querySelectorAll("button,[role=button],[class*=btn],a")]
            .filter((b) => (b.innerText || "").trim() === "立即购买").length;
        if (prices > 1 || buys > 1) return false;
        return [...p.querySelectorAll("button,[role=button],[class*=btn],a")]
            .some((b) => ok(b.innerText));
    });
    document.querySelectorAll("[" + tag + "]").forEach((e) => e.removeAttribute(tag));
    if (!panels.length) return null;
    // a full-screen overlay is never the dialog -- the page's own masks hold
    // the product card and sometimes a stray 确定, and clicking that is
    // exactly the mistake that lost the first order
    const area = (e) => {
        const r = e.getBoundingClientRect();
        return r.width * r.height;
    };
    const viewport = window.innerWidth * window.innerHeight;
    const pool = panels.filter((p) => area(p) < viewport * 0.8);
    if (!pool.length) return null;
    pool.sort((a, b) => area(a) - area(b));
    const p = pool[0];
    p.setAttribute(tag, "1");
    const r = p.getBoundingClientRect();
    const btns = [...p.querySelectorAll("button,[role=button],[class*=btn],a")]
        .map((b) => (b.innerText || "").trim())
        .filter((x) => x && x.length < 20);
    return {text: (p.innerText || "").replace(/\\n+/g, " | ").slice(0, 300),
            buttons: [...new Set(btns)],
            w: Math.round(r.width), h: Math.round(r.height)};
}
""")

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


def handle_purchase(context, page, seconds=25):
    """Wait for the buy dialog and confirm it -- in whichever tab it opened.

    The confirm click must land on OUR dialog: on the first attempt a stray
    确定 elsewhere on the page was clicked, the dialog closed, and no order
    was ever created. So the panel has to name our product and carry a
    confirm button, and the region is chosen inside that same panel.

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
                    our_panel = found
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
    note("DIALOG", "giving up: %s" % detail)
    return {"ok": False, "code": code, "detail": detail,
            "panels": seen_panels, "buttons": seen_buttons}


def hold(page):
    """Stop touching the page -- the user takes over. Log only."""
    note("HOLD", "the page is yours now; click 立即抢购 and the confirm button "
                  "yourself, then pay in this window")
    note("HOLD", "I keep logging the requests so we can see the order go through")
    while True:
        page.wait_for_timeout(1000)


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
        order_state = {"order_request": False, "order_response": None}

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
        give_up_at = time.time() + max_wait_min * 60

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
            txt = button_text(page)
            if txt is None:
                # the marked element is gone (scrolled out of the DOM, or
                # React replaced the node) -- find and mark it again
                c = remark(page) or find_target(page, budget=12.0)
                if c:
                    mark(page, c["idx"])
                    note("WAIT", "re-marked on %r" % c["text"])
                    page.wait_for_timeout(200)
                else:
                    note("WAIT", "card not found -- reloading the page")
                    page.reload(wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3000)
                    c = find_target(page, budget=12.0)
                    if c:
                        mark(page, c["idx"])
                        reveal(page, c["idx"])
                continue

            if txt != last_text:
                note("BTN", "target button = %r" % txt)
                last_text = txt

            if is_buy_text(txt):
                attempts += 1
                if flip_at is None:
                    flip_at = time.time()
                    report.fact("button flipped at",
                                time.strftime("%H:%M:%S", time.localtime(flip_at)))
                    note("FIRE", "the button flipped -- timing the click")
                note("FIRE", "attempt %d: %r -- clicking" % (attempts, txt))
                if not click_target(page):
                    note("FIRE", "click did not land")
                    page.wait_for_timeout(200)
                    continue
                click_at = time.time()
                report.fact("click landed at",
                            time.strftime("%H:%M:%S", time.localtime(click_at)))
                report.fact("flip->click (ms)", int((click_at - flip_at) * 1000))
                note("FIRE", "clicked; hunting for the buy dialog")
                result = handle_purchase(context, page, seconds=25)
                last_verdict = result
                if result.get("ok"):
                    bought = True
                    break
                report.fact("dialog verdict", result.get("code"))
                report.fact("dialog detail", result.get("detail"))
                if result.get("buttons"):
                    report.fact("page buttons at that moment",
                                ", ".join(result["buttons"][:25]))
                if attempts >= 3:
                    note("FIRE", "no order after %d attempts -- the page is yours"
                         % attempts)
                    report.fail(result.get("code") or "FAIL_NO_CONFIRM",
                                "试了 %d 次都没成：%s"
                                % (attempts, result.get("detail")))
                    report.shot(page, "fail-after-%d-attempts.png" % attempts)
                    report.write()
                    hold(page)
                    return
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
