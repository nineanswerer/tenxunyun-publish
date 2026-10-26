"""Why a run did or did not produce an order.

The log says what the script *saw*. This says why it *ended the way it did*.

Every run -- success or failure -- writes one report under runs/<stamp>/:

    report.json   the whole thing, machine readable
    report.md     the same thing, one screen, for a human
    *.png         a screenshot, whenever something went wrong

The verdict is a single stable code, so failures can be compared across runs
and counted. The codes:

    OK_ORDER_CREATED     the order exists -- the user still has to pay
    OK_USER_TOOK_OVER    the script stepped aside; the user clicks
    FAIL_NO_CARD         our card was never found on the page
    FAIL_SOLD_OUT        the button said this session is sold out
    FAIL_NO_FLIP         the button never became 立即抢购
    FAIL_CLICK_MISSED    the click did not land on the button
    FAIL_NO_DIALOG       we clicked, but no purchase panel appeared
    FAIL_DIALOG_WRONG    a panel appeared, but it is not our product
    FAIL_NO_CONFIRM      our panel had no button we were willing to click
    FAIL_REGION_MISSING  our panel wants a region we do not offer
    FAIL_ORDER_REJECTED  the order API refused the request
    FAIL_NO_ORDER_REQ    we confirmed, but no order request went out
    FAIL_LOGIN           the page is not logged in
    FAIL_UNKNOWN         it ended badly and we cannot say why
"""

import json
import os
import time

# code -> (short label, what it means, what to do about it)
VERDICTS = {
    "OK_ORDER_CREATED": (
        "订单已生成",
        "按钮翻牌、点击、确认都成功了，页面已经发出下单请求。",
        "在浏览器里付钱。"),
    "OK_USER_TOOK_OVER": (
        "交给你手动",
        "脚本连试几次没成交，主动让位了。",
        "自己点：目标卡 -> 立即抢购 -> 弹窗里的确认 -> 付款。"),
    "OK_SURVEY_ONLY": (
        "只看不点",
        "跑的是 --survey，只扫了页面，什么都没点。",
        "没问题，这是自检模式。"),
    "OK_DIALOG_CONFIRMED": (
        "已点确认",
        "购买框认出来了，确认按钮也点了。",
        "看页面有没有跳到收银台；有就付款。"),
    "FAIL_NO_CARD": (
        "没找到目标卡",
        "页面上没扫到我们要的那张卡（规格+价格都对不上）。",
        "跑 check_target.py 看页面上到底有什么；多半是配置里的价格或规格写错了。"),
    "FAIL_SOLD_OUT": (
        "已抢光",
        "按钮自己说这一场卖完了。",
        "换下一场，或者手动看看别的地域/规格还有没有。"),
    "FAIL_NO_FLIP": (
        "按钮没翻牌",
        "一直等到结束，按钮也没变成「立即抢购」。",
        "看 SCHED 那几行：场次时间对不对；页面有没有正常加载（NAV 那行）。"),
    "FAIL_CLICK_MISSED": (
        "点击没落地",
        "脚本点了，但按钮还是「立即抢购」。",
        "通常是被页面上盖住的东西挡了；截图会告诉你当时页面长什么样。"),
    "FAIL_NO_DIALOG": (
        "没弹购买框",
        "点了「立即抢购」，但购买框没出来。",
        "多半是这一场本来就没库存，或者页面弹的是别的东西；截图会告诉你。"),
    "FAIL_DIALOG_WRONG": (
        "弹窗不是我们的",
        "弹出了框，但里面的商品/价格不是我们要的那一个。",
        "把报告里的面板文字发出来，照着调 BUY_MARKERS / 价格门槛。"),
    "FAIL_NO_CONFIRM": (
        "框里没有能点的确认键",
        "框认出来了，但里面没有我们敢点的确认按钮。",
        "看报告里的 buttons 列表；把正确的按钮词加进 config.json 的 confirm_words。"),
    "FAIL_REGION_MISSING": (
        "找不到地域",
        "框里要选地域，但配置里的地域一个都没匹配上。",
        "改 config.json 的 regions（要和页面上写的一模一样）。"),
    "FAIL_ORDER_REJECTED": (
        "接口拒了",
        "点了确认，但下单接口返回了失败。",
        "报告里有接口的原始返回，看 code / msg。"),
    "FAIL_NO_ORDER_REQ": (
        "点了但没下单",
        "点了确认，可页面从头到尾没发下单请求。",
        "多半是框没认对，或者库存已经没了；截图会告诉你。"),
    "FAIL_LOGIN": (
        "没登录",
        "页面跳到登录页了。",
        "跑 1-login.bat 重新扫码登录。"),
    "FAIL_UNKNOWN": (
        "原因不明",
        "结束得不对，但没能归类。",
        "把 report.md 和截图发出来，一起看。"),
}

CODE_OK = "OK_ORDER_CREATED"
CODE_USER = "OK_USER_TOOK_OVER"


class Report:
    """Collects what happened and writes it out when the run ends."""

    def __init__(self, run_id=None, out_dir="runs", t0=None):
        self.t0 = time.time() if t0 is None else t0
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
        self.out_dir = out_dir
        self.dir = os.path.join(out_dir, self.run_id)
        self.events = []
        self.facts = {}
        self.verdict = None
        self.detail = ""
        self.shots = []
        self.written = False

    # ---- collecting ---------------------------------------------------

    def event(self, tag, msg, at=None):
        """One line of the timeline (the same lines the log gets).

        `at` is a "HH:MM:SS" wall clock, used when the line comes from
        somewhere else -- a log file that is being read back, say.
        """
        if at:
            hh, mm, ss = at.split(":")
            secs = int(hh) * 3600 + int(mm) * 60 + int(ss)
            base = time.localtime(self.t0)
            base_secs = base.tm_hour * 3600 + base.tm_min * 60 + base.tm_sec
            rel = secs - base_secs
            if rel < -43200:        # crossed midnight
                rel += 86400
            elif rel > 43200:
                rel -= 86400
        else:
            rel = time.time() - self.t0
        self.events.append({
            "t": round(rel, 3),
            "at": at or time.strftime("%H:%M:%S"),
            "tag": tag,
            "msg": str(msg),
        })

    def fact(self, key, value):
        """A durable fact: times, ids, what an API answered."""
        self.facts[key] = value

    def set(self, code, detail=""):
        """The verdict. The first call wins -- the end of the run decides."""
        if self.verdict is None:
            self.verdict = code
            self.detail = detail
        return self.verdict

    def ok(self, detail=""):
        return self.set(CODE_OK, detail)

    def fail(self, code, detail=""):
        return self.set(code, detail)

    def shot(self, page, name):
        """A screenshot of the page right now -- the money shot when it fails."""
        try:
            os.makedirs(self.dir, exist_ok=True)
            path = os.path.join(self.dir, name)
            page.screenshot(path=path, full_page=False)
            self.shots.append(os.path.basename(path))
            self.event("SHOT", name)
            return path
        except Exception as exc:
            self.event("SHOT", "could not take a screenshot (%s)" % exc)
            return None

    # ---- writing ------------------------------------------------------

    def _markdown(self):
        code = self.verdict or "FAIL_UNKNOWN"
        label, means, todo = VERDICTS.get(code, VERDICTS["FAIL_UNKNOWN"])
        out = []
        out.append("# 抢购结果：%s" % label)
        out.append("")
        out.append("- **判定码**：`%s`" % code)
        out.append("- **开始**：%s（本地时间）"
                   % time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(self.t0)))
        out.append("- **时长**：%.1f 秒" % (time.time() - self.t0))
        if self.detail:
            out.append("- **具体**：%s" % self.detail)
        out.append("")
        out.append("## 这是什么意思")
        out.append("")
        out.append(means)
        out.append("")
        out.append("## 接下来怎么办")
        out.append("")
        out.append(todo)
        if self.facts:
            out.append("")
            out.append("## 关键事实")
            out.append("")
            out.append("| 项 | 值 |")
            out.append("| --- | --- |")
            for k, v in self.facts.items():
                out.append("| %s | `%s` |" % (k, str(v).replace("|", "\\|")
                                              .replace("\n", " ")[:300]))
        if self.shots:
            out.append("")
            out.append("## 截图")
            out.append("")
            for s in self.shots:
                out.append("- `%s`" % s)
        out.append("")
        out.append("## 时间线")
        out.append("")
        out.append("```text")
        # the head matters as much as the tail: START / SCHED / CARD / TARGET
        # / FIRE are at the beginning, and a long DIALOG hunt can bury them
        head, tail = self.events[:15], self.events[-25:]
        if len(self.events) > len(head) + len(tail):
            skipped = len(self.events) - len(head) - len(tail)
            for e in head:
                out.append("[%s] %-5s %s" % (e["at"], e["tag"], e["msg"]))
            out.append("… 省略 %d 条 …" % skipped)
            for e in tail:
                out.append("[%s] %-5s %s" % (e["at"], e["tag"], e["msg"]))
        else:
            for e in self.events:
                out.append("[%s] %-5s %s" % (e["at"], e["tag"], e["msg"]))
        out.append("```")
        out.append("")
        return "\n".join(out)

    def write(self):
        """Write the report. Safe to call twice."""
        if self.written:
            return self.dir
        try:
            os.makedirs(self.dir, exist_ok=True)
            code = self.verdict or "FAIL_UNKNOWN"
            label = VERDICTS.get(code, VERDICTS["FAIL_UNKNOWN"])[0]
            payload = {
                "run_id": self.run_id,
                "verdict": code,
                "verdict_label": label,
                "detail": self.detail,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                           time.localtime(self.t0)),
                "duration_seconds": round(time.time() - self.t0, 1),
                "facts": self.facts,
                "shots": self.shots,
                "events": self.events,
            }
            with open(os.path.join(self.dir, "report.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            with open(os.path.join(self.dir, "report.md"), "w",
                      encoding="utf-8") as fh:
                fh.write(self._markdown())
            self.written = True
        except Exception as exc:
            self.event("REPORT", "could not write the report (%s)" % exc)
        return self.dir


# A module-level report, so every part of the script can reach it without
# threading it through every function.
REPORT = None


def get():
    """The current report, created on first use."""
    global REPORT
    if REPORT is None:
        REPORT = Report()
    return REPORT
