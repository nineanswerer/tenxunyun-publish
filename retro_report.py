"""Rebuild a report from a log that was written before reports existed.

The 2026-10-26 run failed and the log was all we had -- it said what the
script *saw*, but not why it *ended*. This reads that log back and writes
the same report the script would have written, so the old failure can be
read in the same shape as every future one.

    .venv\\Scripts\\python.exe retro_report.py snap_browser.log \\
        --run-id 20261026-150000-lost --out runs

The verdict is inferred from the log, but you can override it:

    .venv\\Scripts\\python.exe retro_report.py snap_browser.log \\
        --verdict FAIL_NO_CONFIRM --detail "..."
"""
import argparse
import os
import re
import sys
import time

import snap_report

sys.stdout.reconfigure(encoding="utf-8")

LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\]\s+(\S+)\s+(.*)$")

# The tags worth keeping. REQ/RES is noise: hundreds of lines of page
# furniture would bury the four lines that matter.
KEEP = ("START", "SCHED", "CARD", "TARGET", "WAIT", "BTN", "FIRE",
        "ORDER", "RESP", "SHOT", "REPORT", "NOTE")

ORDER_PATHS = ("do-goods", "to-settle")


def parse(path):
    """The log -> [(time, tag, message)]."""
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            m = LINE.match(raw.rstrip("\n"))
            if m:
                out.append((m.group(1), m.group(2), m.group(3)))
    return out


def infer_verdict(lines):
    """What the log says happened."""
    tags = [t for _, t, _ in lines]
    body = "\n".join(m for _, _, m in lines)

    if any(p in body for p in ORDER_PATHS):
        return "OK_ORDER_CREATED", "下单请求发出去了。"
    if "FIRE" not in tags:
        if "FAIL_NO_FLIP" in body:
            return "FAIL_NO_FLIP", "按钮一直没翻牌。"
        return "FAIL_UNKNOWN", "日志里没有点击记录。"
    if not any(t == "DIALOG" for t in tags):
        return "FAIL_NO_DIALOG", "点了之后没有任何弹窗被认出来。"

    # a dialog was seen -- what was in it?
    buttons = ""
    for _, _, msg in lines:
        if msg.startswith("buttons="):
            buttons = msg
    has_our_price = "38" in body
    if has_our_price and "确认购买" in buttons:
        return "FAIL_NO_CONFIRM", "认出了我们的框，但框里没有确认键。"
    return "FAIL_DIALOG_WRONG", "框不是我们的商品。"


def build(lines, run_id, out_dir, t0=None):
    r = snap_report.Report(run_id=run_id, out_dir=out_dir, t0=t0)

    last_dialog = {}
    for at, tag, msg in lines:
        if tag == "DIALOG":
            # the dialog hunt repeats every second; keep the last look at
            # each kind of line (the panel itself, and its button list)
            key = "buttons" if msg.startswith("buttons=") else "panel"
            last_dialog[key] = (at, msg)
            continue
        if tag in ("REQ", "RES", "BODY"):
            continue
        if tag not in KEEP:
            continue
        r.event(tag, msg, at=at)
    for key in ("panel", "buttons"):
        if key in last_dialog:
            at, msg = last_dialog[key]
            r.event("DIALOG", msg, at=at)

    # ---- facts -----------------------------------------------------------
    for at, tag, msg in lines:
        if tag == "SCHED" and msg.startswith("our session:"):
            r.fact("session", msg.split(":", 1)[1].strip())
        elif tag == "START":
            m = re.search(r"cookies:\s*(\d+)", msg)
            if m:
                r.fact("cookies", m.group(1))
        elif tag == "TARGET" and "button" in msg:
            r.fact("target button", msg.split("button", 1)[1].strip()[:120])
        elif tag == "FIRE" and "attempt" in msg:
            r.fact("flip->click", msg.strip())
        elif tag == "REQ" and any(p in msg for p in ORDER_PATHS):
            r.fact("order request", "有")
        elif tag == "BODY" and "prepare-do" in msg:
            r.fact("page request after the click", "POST /dianshi/prepare-do")
    for at, tag, msg in lines:
        if tag == "BODY" and "prepare-do" in msg:
            m = re.search(r"act_id=(\d+)", msg)
            if m:
                r.fact("act_id", m.group(1))
            break
    if not any(k == "order request" for k in r.facts):
        r.fact("order request", "没有 —— 全程未发出")
    for at, tag, msg in reversed(lines):
        if tag == "DIALOG" and msg.startswith("buttons="):
            r.fact("page buttons at that moment", msg[len("buttons="):].strip()[:300])
            break
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", nargs="?", default="snap_browser.log")
    ap.add_argument("--run-id", default=None,
                    help="folder name under runs/ (default: from the log)")
    ap.add_argument("--out", default="runs")
    ap.add_argument("--verdict", default=None, help="override the verdict")
    ap.add_argument("--detail", default=None, help="override the detail")
    args = ap.parse_args()

    if not os.path.isfile(args.log):
        print("no such log: %s" % args.log)
        return 1

    lines = parse(args.log)
    if not lines:
        print("no lines in %s" % args.log)
        return 1

    first_at = lines[0][0]
    hh, mm, ss = (int(x) for x in first_at.split(":"))
    t0 = time.mktime(time.localtime()[:3] + (hh, mm, ss, 0, 0, -1))

    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S", time.localtime(t0))
    r = build(lines, run_id, args.out, t0)

    code, detail = infer_verdict(lines)
    if args.verdict:
        code = args.verdict
    if args.detail:
        detail = args.detail
    r.set(code, detail)

    path = r.write()
    print("wrote %s" % path)
    print("verdict: %s" % code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
