"""The report must always say why the run ended.

If this file fails, the next lost order will be just as mysterious as the
last one -- so it is worth checking that the report is written, that the
verdict is in it, and that every code we use has an explanation.
"""

import json
import os
import shutil
import sys
import tempfile

import snap_report

sys.stdout.reconfigure(encoding="utf-8")

OK = True


def check(cond, what):
    global OK
    if cond:
        print("  ok   %s" % what)
    else:
        print("  FAIL %s" % what)
        OK = False


def main():
    tmp = tempfile.mkdtemp(prefix="snapreport-")
    try:
        print("--- a failed run writes a report ---")
        r = snap_report.Report(run_id="test-run", out_dir=tmp)
        r.event("START", "b5  cookies: 35")
        r.event("FIRE", "attempt 1: '立即抢购' -- clicking")
        r.fact("session", "2026-10-26 15:00:00-18:00:00")
        r.fact("flip->click (ms)", 143)
        r.fail("FAIL_NO_CONFIRM",
               "our panel was found (1420x409) but it carries no button "
               "we are willing to click")
        path = r.write()

        check(os.path.isfile(os.path.join(path, "report.json")),
              "report.json was written")
        check(os.path.isfile(os.path.join(path, "report.md")),
              "report.md was written")

        with open(os.path.join(path, "report.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        check(data["verdict"] == "FAIL_NO_CONFIRM",
              "the verdict is in the json -- %s" % data["verdict"])
        check(data["facts"].get("flip->click (ms)") == 143,
              "the facts are in the json")
        check(len(data["events"]) == 2, "the timeline is in the json")

        with open(os.path.join(path, "report.md"), encoding="utf-8") as fh:
            md = fh.read()
        check("FAIL_NO_CONFIRM" in md, "the verdict code is in the markdown")
        check("这是什么意思" in md, "the markdown explains what it means")
        check("接下来怎么办" in md, "the markdown says what to do")

        print("\n--- a run keeps the FIRST verdict ---")
        r2 = snap_report.Report(run_id="test-run-2", out_dir=tmp)
        r2.fail("FAIL_NO_CARD", "first")
        r2.ok("second, too late")
        check(r2.verdict == "FAIL_NO_CARD",
              "the first verdict wins -- %s" % r2.verdict)

        print("\n--- every verdict code has an explanation ---")
        missing = [c for c in snap_report.VERDICTS
                   if len(snap_report.VERDICTS[c]) != 3
                   or not all(snap_report.VERDICTS[c])]
        check(not missing, "every code has a label, a meaning and a next step"
              + (" -- %s" % missing if missing else ""))
        check(len(snap_report.VERDICTS) >= 10,
              "there are %d codes" % len(snap_report.VERDICTS))

        print("\n--- the codes the browser script can produce are all known ---")
        import re
        src = open("snap_browser.py", encoding="utf-8").read()
        used = set(re.findall(r'"(FAIL_[A-Z_]+)"', src))
        used |= set(re.findall(r'"(OK_[A-Z_]+)"', src))
        unknown = sorted(c for c in used if c not in snap_report.VERDICTS)
        check(not unknown, "no unknown code is used -- %s" % (unknown or "none"))

        print("\n--- an old log can be turned back into a report ---")
        import retro_report
        log = os.path.join(tmp, "old.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("[00:52:27] START b5  cookies: 35\n")
            fh.write("[00:52:35] SCHED our session: 2026-10-26 15:00:00 "
                     "未开始\n")
            fh.write("[01:00:01] FIRE  attempt 1: '立即抢购' -- clicking\n")
            fh.write("[01:00:01] REQ   POST act-api.cloud.tencent.com/dianshi/"
                     "prepare-do\n")
            fh.write("[01:00:01] BODY  POST /dianshi/prepare-do "
                     "{act_id=1996855673581677}\n")
            fh.write("[01:00:15] DIALOG our dialog 1420x409  ... 立即购买\n")
            fh.write("[01:00:15] DIALOG buttons=['立即购买']\n")
        lines = retro_report.parse(log)
        check(len(lines) == 7, "the log was parsed -- %d lines" % len(lines))
        code, detail = retro_report.infer_verdict(lines)
        check(code == "FAIL_DIALOG_WRONG" or code == "FAIL_NO_CONFIRM",
              "the verdict was inferred -- %s" % code)
        r3 = retro_report.build(lines, "retro", tmp, 0)
        r3.set(code, detail)
        p3 = r3.write()
        with open(os.path.join(p3, "report.md"), encoding="utf-8") as fh:
            md3 = fh.read()
        check("00:52:27" in md3,
              "the report keeps the log's own times, not the build time")
        check("1996855673581677" in md3, "the act_id made it into the facts")

        print("\n--- a run that dies still leaves a report ---")
        import snap_browser
        real_run = snap_browser._run

        def boom():
            raise RuntimeError("the browser fell over")

        snap_browser._run = boom
        # the crash report goes to the temp dir, not to the real runs/
        snap_report.REPORT = snap_report.Report(run_id="crash-test",
                                                out_dir=tmp)
        try:
            snap_browser.main()
        except RuntimeError:
            pass
        finally:
            snap_browser._run = real_run
        r4 = snap_report.get()
        check(r4.verdict == "FAIL_UNKNOWN",
              "a crash is reported as FAIL_UNKNOWN -- %s" % r4.verdict)
        check("fell over" in (r4.detail or ""),
              "the crash text is in the report")
        check(r4.written, "the report was written even though the run died")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("all checks passed" if OK else "SOME CHECKS FAILED")
    raise SystemExit(0 if OK else 1)


if __name__ == "__main__":
    main()
