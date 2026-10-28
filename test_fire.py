"""Offline test of the firing logic.

On 2026-10-27 the click was right -- 81 ms after the button flipped, with
the right act_id -- and the page sent the order request. The run never
recorded what the server answered, so nobody could say whether the order
was refused because the sale had not opened yet or because it was gone.
The 25 seconds it then spent hunting for a buy panel that was never going
to appear were the visible symptom.

These checks pin down the parts that can be checked without a browser:

  * "the sale has not started" is recognised, and a good answer is not,
  * a refusal has its own verdict code, and it is explained in the report,
  * a refusal that is not about the timing is still a refusal.

No network, no account, no purchase.

    .venv\\Scripts\\python.exe test_fire.py
"""
import os
import sys
import tempfile
import time

import snap_report
from snap_browser import (BEIJING, EARLY_LEAD, MOVE_CLICK, MOVE_HUNT,
                          MOVE_REFUSED, MOVE_RETRY_AT_OPEN, MOVE_SOLD_OUT,
                          handle_purchase, is_refused, is_sold_out,
                          is_too_early, last_click_at, next_move,
                          next_opening, release_in, reply_for, session_start,
                          should_hold)

sys.stdout.reconfigure(encoding="utf-8")

# the self-test writes its scratch report here, not into runs/
SELFTEST_DIR = os.path.join(tempfile.gettempdir(), "snap-selftest")

OK = 0


# --------------------------------------------------------------------------
# a fake page -- just enough of Playwright to drive handle_purchase
# --------------------------------------------------------------------------
class FakeFrame:
    """Answers the four JavaScript snippets handle_purchase runs.

    `panel` is what the dialog finder returns: None means no buy panel
    ever shows up. Every confirm click is recorded, with the time.
    """

    def __init__(self, panel=None):
        self.panel = panel
        self.confirmed_at = []
        self.page = None

    def evaluate(self, js, arg=None):
        if "priceRe" in js:                     # DIALOG_FIND_JS
            return self.panel
        if "regions" in js:                     # DIALOG_REGION_JS
            return None
        if "strong" in js:                      # DIALOG_CONFIRM_JS
            # the real script falls back to a bare order word on the page
            # when no panel was tagged; this fake page has none
            if self.panel is None:
                return None
            self.confirmed_at.append(time.time())
            return {"text": "确认购买", "where": "our dialog"}
        return {"panels": [], "buttons": []}    # DUMP_JS

    def wait_for_timeout(self, ms):
        time.sleep(min(ms, 50) / 1000.0)


class FakePage:
    def __init__(self, frame):
        self.main_frame = frame
        self.frames = []
        self.url = "https://example.invalid/"
        frame.page = self

    def wait_for_timeout(self, ms):
        time.sleep(min(ms, 50) / 1000.0)


class FakeContext:
    def __init__(self, page):
        self.pages = [page]


def check(cond, what):
    global OK
    if cond:
        OK += 1
        print("  ok   %s" % what)
    else:
        print("  FAIL %s" % what)


def main():
    print("--- the server says the sale has not started ---")
    check(is_too_early({"code": 1101001, "msg": "not started"}),
          "1101001 / not started is recognised")
    check(is_too_early({"code": 1101001, "msg": "秒杀尚未开始"}),
          "1101001 / 秒杀尚未开始 is recognised")
    check(is_too_early({"code": "1101001", "msg": ""}),
          "the code is recognised as a string too")
    check(is_too_early({"code": None, "msg": "not started"}),
          "the message alone is enough")

    print("\n--- a good answer is not a refusal ---")
    check(not is_too_early({"code": 0, "msg": "ok"}),
          "code 0 is not 'too early'")
    check(not is_too_early({"code": 200, "msg": ""}),
          "code 200 is not 'too early'")
    check(not is_too_early({"code": None, "msg": ""}),
          "no code is not 'too early'")
    check(not is_too_early(None),
          "no answer at all is not 'too early'")

    print("\n--- another refusal is a refusal, just not a timing one ---")
    other = {"code": 500, "msg": "system error"}
    check(not is_too_early(other),
          "a server error is not 'too early'")

    print("\n--- the refusal has its own verdict code ---")
    for code in ("FAIL_PREPARE_REJECTED", "FAIL_SOLD_OUT"):
        check(code in snap_report.VERDICTS,
              "%s is a known verdict code" % code)
        if code in snap_report.VERDICTS:
            label, means, todo = snap_report.VERDICTS[code]
            check(bool(label) and bool(means) and bool(todo),
                  "%s has a label, a meaning and a next step" % code)

    print("\n--- the click waits for the session to open ---")
    # the session of 2026-10-27: it opened at 20:00 local (10:00 Beijing)
    later = {"date": "2026-10-28", "start": "10:00:00", "end": "12:00:00"}
    earlier = {"date": "2026-10-27", "start": "10:00:00", "end": "12:00:00"}

    def at(y, m, d, hh, mm):
        import datetime
        return datetime.datetime(y, m, d, hh, mm,
                                 tzinfo=BEIJING).timestamp()

    st_later = session_start(later)
    sched = [(session_start(earlier), earlier), (st_later, later)]

    # 09:59 Beijing -- the session opens in one minute: that is the time
    # the click has to wait for
    now = at(2026, 10, 28, 9, 59)
    check(next_opening(sched, now) == st_later.timestamp(),
          "a session that opens later is the one we wait for")

    # 11:00 Beijing -- the session is running: the click must go NOW
    now = at(2026, 10, 28, 11, 0)
    check(next_opening(sched, now) is None,
          "a session that is running right now is not waited for")

    # 13:00 Beijing -- every session is over
    now = at(2026, 10, 28, 13, 0)
    check(next_opening(sched, now) is None,
          "a session that is over is not waited for")

    check(next_opening([], now) is None,
          "no session at all means nothing to wait for")

    print("\n--- the click is held only while the button has just armed ---")
    opens = at(2026, 10, 28, 10, 0)
    # the button armed one second before the opening (what the page does)
    check(should_hold(opens - 1, opens, opens - 1),
          "a button that armed a second ago is held back")
    check(not should_hold(opens - 1, opens, opens),
          "once the opening has come the click goes out")
    check(not should_hold(opens - 600, opens, opens - 1),
          "a button that has been armed for a long time is not held back")
    check(not should_hold(opens - 1, None, opens - 1),
          "with no opening time there is nothing to hold back for")

    print("\n--- the server says the goods are gone ---")
    # this is what the API answered on 2026-10-28, 01:00:03 -- three
    # seconds after the sale opened, to a click that landed exactly on it
    sold = {"code": 1100136, "msg": "抱歉，当前商品已售罄，欢迎选择其他商品",
            "text": "{\"code\":1100136,\"available\":0}"}
    check(is_sold_out(sold), "1100136 / 已售罄 is recognised")
    check(is_sold_out({"code": "1100136", "msg": ""}),
          "the code is recognised as a string too")
    check(is_sold_out({"code": None, "msg": "当前商品已售罄"}),
          "the message alone is enough")
    check(is_sold_out({"code": None, "msg": "sorry, this item is sold out"}),
          "the english wording is recognised too")
    check(not is_sold_out({"code": 1101001, "msg": "not started"}),
          "'not started' is not 'sold out'")
    check(not is_sold_out({"code": 0, "msg": "ok"}),
          "a good answer is not 'sold out'")
    check(not is_sold_out(None),
          "no answer at all is not 'sold out'")

    print("\n--- a killed run is interrupted, not 'unknown' ---")
    check("FAIL_INTERRUPTED" in snap_report.VERDICTS,
          "FAIL_INTERRUPTED is a known verdict code")
    if "FAIL_INTERRUPTED" in snap_report.VERDICTS:
        label, means, todo = snap_report.VERDICTS["FAIL_INTERRUPTED"]
        check(bool(label) and bool(means) and bool(todo),
              "FAIL_INTERRUPTED has a label, a meaning and a next step")
    r = snap_report.Report(run_id="test-interrupted", out_dir=SELFTEST_DIR)
    r.fact("the server's answer", "HTTP 200 code=1100136 msg=售罄")
    r.write()
    again = snap_report.Report(run_id="test-interrupted", out_dir=SELFTEST_DIR)
    again.fact("the server's answer", "HTTP 200 code=1100136 msg=售罄")
    again.write()
    import json
    with open(os.path.join(again.dir, "report.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    check(payload["verdict"] == "FAIL_INTERRUPTED",
          "a run that was killed is reported as interrupted -- %s"
          % payload["verdict"])
    check(payload["facts"].get("the server's answer"),
          "the facts survive being written again")

    print("\n--- what the run does after a hunt ---")
    # the whole point of b7: a click that went out before the sale opened
    # and was refused is NOT a lost order -- the run goes again
    opens = 1000.0
    early = {"code": 1101001, "msg": "秒杀尚未开始"}
    sold = {"code": 1100136, "msg": "抱歉，当前商品已售罄"}
    other = {"code": 500, "msg": "system error"}
    good = {"code": 0, "msg": ""}

    check(next_move(sold, opens + 0.5, opens) == MOVE_SOLD_OUT,
          "sold out ends the run")
    check(next_move(sold, opens - 1.0, opens) == MOVE_SOLD_OUT,
          "sold out ends the run even when the click was early")
    check(next_move(early, opens - 1.0, opens) == MOVE_RETRY_AT_OPEN,
          "a refusal for a click that went out early means: go again")
    check(next_move(other, opens - 1.0, opens) == MOVE_RETRY_AT_OPEN,
          "any refusal for an early click means: go again")
    check(next_move(early, opens + 0.5, opens) == MOVE_REFUSED,
          "a refusal after the sale opened is final")
    check(next_move(other, opens + 0.5, opens) == MOVE_REFUSED,
          "any refusal after the sale opened is final")
    check(next_move(None, opens + 0.5, opens) == MOVE_HUNT,
          "no answer at all means: keep looking")
    check(next_move(good, opens + 0.5, opens) == MOVE_CLICK,
          "a good answer but no panel means: click again")
    check(next_move(early, opens + 0.5, None) == MOVE_REFUSED,
          "with no opening time a refusal is final")

    print("\n--- a refusal ends the hunt before the second click ---")
    frame = FakeFrame(None)
    page = FakePage(frame)
    ctx = FakeContext(page)
    clicks = []

    def refused():
        return ("FAIL_PREPARE_REJECTED", "the server refused the order")

    res = handle_purchase(ctx, page, seconds=3, stop=refused,
                          again_at=time.time() + 0.5,
                          clicker=lambda: clicks.append(time.time()))
    check(res.get("code") == "FAIL_PREPARE_REJECTED",
          "the refusal ends the hunt")
    check(not clicks,
          "no second click -- the hunt ended so the run can go again")

    print("\n--- the second click goes out at the opening, not later ---")
    clicks = []
    open_at = time.time() + 0.4
    res = handle_purchase(ctx, page, seconds=3, stop=lambda: None,
                          again_at=open_at,
                          clicker=lambda: clicks.append(time.time()))
    check(len(clicks) == 1, "the second click went out once -- %d" % len(clicks))
    check(bool(clicks) and clicks[0] >= open_at - 0.05,
          "the second click was not late")
    check(bool(clicks) and clicks[0] < open_at + 0.3,
          "the second click was not early either")

    print("\n--- every answer belongs to the click that made it ---")
    # the early click and the click at the opening are two requests, and
    # their answers come back in order -- a stale refusal must not be
    # mistaken for the answer to the click that is in flight
    st = {"prepare_replies": [], "clicks": 0, "click_ats": []}
    check(reply_for(st, 1) is None, "with no click there is no answer")
    st["clicks"] = 1
    st["click_ats"].append(100.0)
    st["prepare_replies"].append({"code": 1101001, "msg": "not started"})
    check((reply_for(st, 1) or {}).get("code") == 1101001,
          "the first answer is the first click's")
    check(reply_for(st, 2) is None,
          "the second click has no answer yet")
    st["clicks"] = 2
    st["click_ats"].append(101.0)
    st["prepare_replies"].append({"code": 1100136, "msg": "sold out"})
    check((reply_for(st, 2) or {}).get("code") == 1100136,
          "the second answer is the second click's")
    newest = reply_for(st, st.get("clicks"))
    check(is_sold_out(newest),
          "the run acts on the newest click's answer, not on the stale one")
    check(last_click_at(st) == 101.0,
          "the time of the newest click is known")
    check(last_click_at({"click_ats": []}) is None,
          "with no click there is no time")

    print("\n--- every code the browser can produce is known ---")
    import re
    src = open("snap_browser.py", encoding="utf-8").read()
    used = set(re.findall(r'"(FAIL_[A-Z_]+)"', src))
    used |= set(re.findall(r'"(OK_[A-Z_]+)"', src))
    unknown = sorted(c for c in used if c not in snap_report.VERDICTS)
    check(not unknown, "no unknown code is used -- %s" % (unknown or "none"))

    print("\n--- a refusal is a refusal ---")
    check(is_refused({"code": 1101001, "msg": "秒杀尚未开始"}),
          "'not started' is a refusal")
    check(is_refused({"code": 1100136, "msg": "已售罄"}),
          "'sold out' is a refusal")
    check(is_refused({"code": "500", "msg": ""}),
          "a refusal whose code is a string is a refusal")
    check(not is_refused({"code": 0, "msg": "ok"}),
          "code 0 is not a refusal")
    check(not is_refused({"code": 200, "msg": "ok"}),
          "code 200 is not a refusal")
    check(not is_refused({"code": None, "msg": "ok"}),
          "no code at all is not a refusal")
    check(not is_refused(None),
          "no answer at all is not a refusal")

    print("\n--- the click is released before the opening, not on it ---")
    # on 2026-10-28 the click went out exactly on the opening and the
    # server answered 'sold out' three seconds later
    opens = time.time() + 60.0
    check(abs(release_in(opens, opens - 10) - (10 - EARLY_LEAD)) < 0.001,
          "the click is held until %0.1f s before the opening" % EARLY_LEAD)
    check(release_in(opens, opens - EARLY_LEAD) == 0,
          "the click goes out %0.1f s before the opening" % EARLY_LEAD)
    check(release_in(opens, opens + 1) == 0,
          "once the opening has passed the click goes out")
    check(release_in(None) == 0,
          "with no opening time there is nothing to wait for")

    print("\n--- the panel is not confirmed before the sale opens ---")
    panel = {"text": "our buy panel", "w": 300, "h": 200,
             "buttons": ["确认购买"]}
    frame = FakeFrame(panel)
    page = FakePage(frame)
    ctx = FakeContext(page)
    open_at = time.time() + 1.0
    res = handle_purchase(ctx, page, seconds=4, not_before=open_at)
    check(res.get("ok"), "the panel was confirmed in the end")
    check(bool(frame.confirmed_at), "the confirm button was clicked")
    check(bool(frame.confirmed_at) and min(frame.confirmed_at) >= open_at,
          "the confirm did not happen before the sale opened")

    print("\n--- an early click is repeated the moment the sale opens ---")
    frame = FakeFrame(None)          # no buy panel ever shows up
    page = FakePage(frame)
    ctx = FakeContext(page)
    clicks = []

    def clicker():
        clicks.append(time.time())
        return True

    open_at = time.time() + 0.6
    res = handle_purchase(ctx, page, seconds=3, again_at=open_at,
                           clicker=clicker)
    check(len(clicks) == 1, "the click was repeated once -- %d" % len(clicks))
    check(bool(clicks) and clicks[0] >= open_at,
          "the repeated click waited for the opening")
    check(not res.get("ok"),
          "with no panel there is no order")

    print()
    print("%d checks passed" % OK if OK else "SOME CHECKS FAILED")
    raise SystemExit(0 if OK else 1)


if __name__ == "__main__":
    main()
