import json
from urllib.parse import quote

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


from snap_config import ACTIVITY_URL

ACTIVITY_URL = ACTIVITY_URL + "#MS"
LOGIN_URL = f"https://cloud.tencent.com/login?s_url={quote(ACTIVITY_URL, safe='')}"

# 扫码后等待跳转的上限（毫秒）。原版用 timeout=0 无限等待，
# 一旦腾讯云不按 s_url 跳回活动页就会永久挂起。
LOGIN_TIMEOUT_MS = 300_000


def refresh_cookies():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URL)

        print("Please scan the QR code to sign in to Tencent Cloud...")
        # 登录后腾讯云不一定按 s_url 跳回活动页（实测会落到首页），
        # 只要离开 /login 且仍在 cloud.tencent.com 域内，就说明登录成功。
        try:
            page.wait_for_url(
                lambda url: "cloud.tencent.com" in url and "/login" not in url,
                timeout=LOGIN_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError:
            # URL 没变但可能已登录成功，交给下面的 Cookie 完整性校验判断。
            print("Login redirect timed out; checking cookies anyway...")

        # Give Tencent Cloud time to finish setting post-login session cookies.
        page.wait_for_timeout(8000)
        cookies = context.cookies()
        cookie_names = {cookie["name"] for cookie in cookies}
        missing = []
        if "uin" not in cookie_names:
            missing.append("uin")
        if not ({"skey", "p_skey"} & cookie_names):
            missing.append("skey/p_skey")
        if missing:
            raise RuntimeError(
                "Login cookies are incomplete (missing: %s). "
                "请确认已在手机端点击确认登录，然后重新运行。" % ", ".join(missing)
            )

        with open("cookies.json", "w", encoding="utf-8") as cookie_file:
            json.dump(cookies, cookie_file, ensure_ascii=False, indent=2)

        print(f"Login succeeded. Saved {len(cookies)} cookies to cookies.json.")
        browser.close()


if __name__ == "__main__":
    refresh_cookies()
