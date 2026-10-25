"""Buy a flash-sale server straight from the live campaign page.

Why a browser and not a plain HTTP script: the page signs its purchase
request (the request layer computes a signature from the server clock, the
request body, the user agent and the skey cookie). A hand-written POST
cannot produce that signature, so it would be rejected. The browser already
carries the signing code, so letting the page make its own request is both
simpler and more reliable.

The button is

    <div class="uno3-buy-card__btn">添加提醒</div>

which becomes

    <div class="uno3-buy-card__btn">立即抢购</div>

while the session runs, and 已抢光 once the stock is gone. Which card it is,
how it is marked and how it is clicked all live in snap_target.py -- see
test_snap_target.py for the offline proof that the right button is chosen.

Which product, which price and which page are NOT decided here: they come
from config.json (see snap_config.py).

Flow:
  1. open the activity page with the saved cookies
  2. read the flash-sale schedule out of the page (which session is running,
     which one is next)
  3. scroll the module that offers our product into view -- the cards are
     skeletons until then -- and find our card's button
  4. when that button says 立即抢购, click it and confirm the buy dialog
  5. leave the browser open so the order can be paid

Every request is logged as METHOD host/path plus the KEY NAMES of its body.
No headers, no cookies, no response bodies are recorded.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

import snap_config
from snap_config import price_pattern
from snap_target import (BTN_ATTR, BTN_CLASS, BUY_WORDS, CARD_ATTR,
                         CONFIRM_WORDS, GROUPS_JS, IDX_ATTR, MARK_ATTR,
                         MARK_JS, RESOLVE_JS, SEKILL_WORDS,
                         is_buy_text, pick, score)

sys.stdout.reconfigure(encoding="utf-8")

HOST_RE = re.compile(r"\.tencent\.com$")
BEIJING = timezone(timedelta(hours=8))
# printed at START so it is always obvious which build is running
BUILD = "b5: the product, the price and the page come from config.json"

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


# --------------------------------------------------------------------------
# element handling (importable, so it can be tested offline)
# --------------------------------------------------------------------------
def scan(page):
    """All seckill candidates, each with the text of its own card."""
    return page.evaluate(RESOLVE_JS,
                         [snap_config.SPEC, snap_config.MARKER,
                          price_pattern(snap_config.PRICE),
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
    flash-sale module is tried first -- it is the one that sells our card,
    and its tab bar names products, not specs.
    """
    end = time.time() + budget
    try:
        groups = page.evaluate(GROUPS_JS, [snap_config.SPEC, snap_config.SECKILL_TITLE])
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
    """Every card that mentions our spec, and what its button says right now.

    The scan returns EVERY button on the page, so the same card comes back
    once per module -- it is de-duplicated here, otherwise the log fills up
    with copies of the same three lines.
    """
    try:
        groups = page.evaluate(GROUPS_JS, [snap_config.SPEC, snap_config.SECKILL_TITLE])
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
# The dialog's class names are not known -- the page gets rebuilt for every
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

# The confirm button, by priority: 确认购买 before 确定. 立即购买 is left out
# on purpose -- it is the label of the *other* cards' buy buttons on this
# very page.
DIALOG_CONFIRM = ("确认购买", "确认下单", "提交订单", "去支付", "立即支付",
                  "确定", "确认")


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
DIALOG_FIND_JS = _js("""
([sel, markers, words, tag]) => {
    const ok = (bt) => {
        bt = (bt || "").trim();
        return bt && bt.length <= 14 &&
               words.some((w) => bt === w || bt.includes(w));
    };
    const panels = [...document.querySelectorAll(sel)].filter((p) => {
        const t = p.innerText || "";
        const r = p.getBoundingClientRect();
        if (!(r.width && r.height)) return false;
        if (!markers.some((m) => t.includes(m))) return false;
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

# The confirm click, in two tiers:
#   1. inside the dialog we tagged -- any of DIALOG_CONFIRM, by priority
#   2. the dialog's class is unknown -- an order word is unambiguous, but
#      only when exactly one such button is on the page
DIALOG_CONFIRM_JS = """
([tag, words, strong]) => {
    const inView = (e) => {
        const r = e.getBoundingClientRect();
        return r.width && r.height &&
               r.bottom > 0 && r.top < window.innerHeight;
    };
    const label = (e) => (e.innerText || "").trim();
    const p = document.querySelector("[" + tag + "='1']");
    if (p) {
        for (const w of words) {
            const b = [...p.querySelectorAll("button,[role=button],[class*=btn],a")]
                .find((e) => {
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
    """
    end = time.time() + seconds
    dumped = set()
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
                    note("DIALOG", "panel %dx%d  %s"
                         % (d["w"], d["h"], d["text"][:260]))
                    note("DIALOG", "  buttons=%s" % d["buttons"])
                buttons = dump.get("buttons") or []
                key = "B " + ",".join(b["text"] for b in buttons)[:80]
                if buttons and key not in dumped:
                    dumped.add(key)
                    note("DIALOG", "page buttons=%s"
                         % ", ".join("%s@%d,%d" % (b["text"], b["x"], b["y"])
                                     for b in buttons[:20]))
                try:
                    found = fr.evaluate(DIALOG_FIND_JS,
                                        [PANEL_SEL,
                                         list(snap_config.dialog_markers()),
                                         list(CONFIRM_WORDS), DLG_TAG])
                except Exception:
                    found = None
                if found:
                    note("DIALOG", "our dialog %dx%d  %s"
                         % (found["w"], found["h"], found["text"][:260]))
                    note("DIALOG", "  buttons=%s" % found["buttons"])
                    try:
                        region = fr.evaluate(DIALOG_REGION_JS,
                                             [DLG_TAG, list(snap_config.REGIONS)])
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
                                       list(STRONG_CONFIRM)])
                except Exception:
                    got = None
                if got:
                    note("DIALOG", "clicked %r in %s" % (got["text"], got["where"]))
                    try:
                        note("DIALOG", "url=%s" % fr.page.url)
                        fr.wait_for_timeout(1800)
                    except Exception:
                        pass
                    return True
        page.wait_for_timeout(250)
    return False


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
    global logf
    args = sys.argv[1:]
    survey_only = "--survey" in args

    if "--config" in args:
        i = args.index("--config")
        if i + 1 >= len(args):
            print("--config needs a file name")
            return
        snap_config.use(args[i + 1])

    logf = open(snap_config.LOG_FILE, "w", encoding="utf-8", buffering=1)

    cookie_file = snap_config.COOKIE_FILE
    if not os.path.isfile(cookie_file):
        note("FATAL", "no %s -- run snap_login.py first and scan the QR code"
             % cookie_file)
        return
    cookies = json.load(open(cookie_file, encoding="utf-8"))
    note("START", "%s  cookies: %d" % (BUILD, len(cookies)))
    note("START", "target: %s / %s / %s元   page: %s"
         % (snap_config.SPEC, snap_config.MARKER, snap_config.PRICE,
            snap_config.PAGE_URL))
    act_id = snap_config.ACT_ID

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        context.add_cookies(cookies)

        seen = set()

        def on_request(req):
            u = urlsplit(req.url)
            if not HOST_RE.search(u.netloc):
                return
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

        context.on("request", on_request)
        context.on("response", on_response)

        page = context.new_page()
        page.on("dialog", lambda d: d.accept())
        page.goto(snap_config.PAGE_URL, wait_until="domcontentloaded", timeout=60000)
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
        else:
            note("SCHED", "our session: %s %s-%s %s"
                 % (target["date"], target["start"], target["end"],
                    target["status_cn"]))

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

        # ---- survey the cards that mention our spec ---------------------
        priced = []
        tag = "%s元" % snap_config.PRICE
        for c in survey(page):
            hit = tag if c.get("hasPrice") else " " * len(tag)
            note("CARD", "%s btn=%-8r score=%-2d %s=%-5s [%s] | %s"
                 % (hit, c["text"], score(c), snap_config.MARKER,
                    c.get("hasMarker"), (c.get("title") or "")[:18], c["card"][:110]))
            if c.get("hasPrice"):
                priced.append(c)
        if priced:
            note("CARD", "==> the %s card's button says %r"
                 % (tag, priced[0]["text"]))
        else:
            note("CARD", "==> no %s card in this pass" % tag)

        if survey_only:
            note("DONE", "survey only -- nothing was clicked")
            browser.close()
            return

        best = find_target(page)
        if not best:
            note("FATAL", "could not find the %s / %s元 card"
                 % (snap_config.SPEC, snap_config.PRICE))
            note("HOLD", "scroll to %s yourself and click 立即抢购"
                 % snap_config.SECKILL_TITLE)
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

        while True:
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
                note("FIRE", "attempt %d: %r -- clicking" % (attempts, txt))
                if not click_target(page):
                    note("FIRE", "click did not land")
                    page.wait_for_timeout(200)
                    continue
                note("FIRE", "clicked; hunting for the buy dialog")
                if handle_purchase(context, page, seconds=25):
                    bought = True
                    break
                if attempts >= 3:
                    note("FIRE", "no order after %d attempts -- the page is yours"
                         % attempts)
                    hold(page)
                    return
                note("FIRE", "no dialog yet -- clicking again")
                page.wait_for_timeout(500)
                continue

            if txt in ("已抢光", "已结束", "已抢完") and sold != txt:
                sold = txt
                note("STATE", "the %s元 card says %r -- sold out in this session"
                     % (snap_config.PRICE, txt))
            elif txt not in ("已抢光", "已结束", "已抢完"):
                sold = None

            page.wait_for_timeout(80)

        note("FIRE", "confirm clicked -- finish paying in this window")
        page.wait_for_timeout(3000)
        note("STATE", "url = %s" % page.url)
        note("NEXT", "browser stays open -- pay in this window")

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
