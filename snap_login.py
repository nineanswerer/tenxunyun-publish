"""Sign in with a QR code and save the cookies for snap_browser.py.

The buyer needs a signed-in session, so this opens the real login page, waits
for you to scan the code with your phone, and writes cookies.json.

    .venv\\Scripts\\python.exe snap_login.py

Two things the site taught us, both worth keeping:

* after the scan the browser does NOT necessarily land on the activity page
  -- it may well end up on the homepage. So "signed in" means "on
  cloud.tencent.com and no longer on /login", not "on the page we asked for".
* there has to be an upper bound: an unbounded wait hangs forever when the
  redirect never happens, and the user is left staring at a window that will
  never close.

Nothing here touches your account beyond the login itself: it only saves the
cookies the browser already holds.
"""
import json
import os
import sys
from urllib.parse import quote

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import snap_config

sys.stdout.reconfigure(encoding="utf-8")

# 扫码后等待跳转的上限（毫秒）
LOGIN_TIMEOUT_MS = 300_000
# 登录成功后多等一会儿，让腾讯云把会话 Cookie 写全
SETTLE_MS = 8000
# 没有这几个 Cookie 就不算登录成功
NEEDED = ("uin",)
NEEDED_ANY = ("skey", "p_skey")


def login_url():
    return "https://cloud.tencent.com/login?s_url=%s" % quote(
        snap_config.PAGE_URL, safe="")


def refresh_cookies():
    out = snap_config.COOKIE_FILE
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(login_url(), timeout=60000)

        print("请用手机扫描浏览器里的二维码，在手机上确认登录……")
        print("(Scan the QR code with your phone and confirm there.)")
        try:
            page.wait_for_url(
                lambda url: "cloud.tencent.com" in url and "/login" not in url,
                timeout=LOGIN_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError:
            # 网址没变，但 Cookie 可能已经写好了 —— 交给下面的完整性检查判断
            print("Login redirect timed out; checking the cookies anyway...")

        page.wait_for_timeout(SETTLE_MS)
        cookies = context.cookies()
        browser.close()

    names = {c["name"] for c in cookies}
    missing = [n for n in NEEDED if n not in names]
    if not (set(NEEDED_ANY) & names):
        missing.append(" or ".join(NEEDED_ANY))
    if missing:
        raise RuntimeError(
            "登录信息不完整（缺少：%s）。请确认已在手机端点击确认登录，然后重新运行。"
            % ", ".join(missing))

    with open(out, "w", encoding="utf-8") as fh:
        json.dump(cookies, fh, ensure_ascii=False, indent=2)
    print("登录成功，已保存 %d 条 Cookie 到 %s。" % (len(cookies), out))


if __name__ == "__main__":
    try:
        refresh_cookies()
    except RuntimeError as exc:
        print(str(exc))
        raise SystemExit(1)
