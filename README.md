# 秒杀助手 —— 让浏览器自己去抢

在云厂商的限时秒杀页面上，**盯着那张卡**，等按钮从「添加提醒」翻成「立即抢购」的
瞬间点下去，认准购买弹窗，确认订单——**然后把付款留给你自己**。

脚本不做的事：**不付款**。订单生成后浏览器停在收银台，你在这个窗口里付钱。
这条规矩写死在代码里（`DIALOG_CONFIRM` 词表里根本没有「立即支付」「去支付」），
不只是在文档上说说。

---

## 为什么用浏览器，而不是直接调接口

当前活动的下单请求是**带签名**的：签名里混了服务器时间、请求体、User-Agent 和
`skey` cookie。纯 HTTP 脚本复现不出这个签名，自己拼的请求会被接口拒掉。

浏览器里已经带着签名的代码，**让页面自己发请求、自己签名**，既是唯一稳的路子，
也是最简单的路子。

> 所以这个仓库里没有纯 HTTP 的抢购脚本。想要那种写法的话，请看文末的原文链接。

---

## 快速开始

```powershell
git clone <this-repo>
cd <this-repo>

py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

**永远用 `.\.venv\Scripts\python.exe` 调脚本**，不要用裸 `python`。缺依赖、版本不对
都会出奇奇怪的问题。

### 三步走

| 步骤 | 命令 | 说明 |
| --- | --- | --- |
| 1. 登录 | `.\.venv\Scripts\python.exe get_cookies.py` | 浏览器打开登录页，扫码。8 秒后写出 `cookies.json` |
| 2. 自检 | `.\.venv\Scripts\python.exe test_snap_target.py` | 离线测试，不联网、不下单 |
| 3. 抢购 | `.\.venv\Scripts\python.exe snap_browser.py` | 提前 5–10 分钟起，**窗口别最小化** |

Windows 上也可以直接双击 `1-login.bat` / `2-dry-run.bat` / `3-start.bat`。

---

## 配置：只改一个文件

`snap_config.py` 是**唯一**和具体商品有关的地方。改完它，整个项目（抢购、定位、
测试）都跟着变。

```python
ACTIVITY_URL = "..."      # 活动页地址
SECKILL_TITLE = "限时秒杀"  # 卡片所在的小节标题
SPEC_TEXT = "4核4G"        # 卡片上印的规格
MARKER_TEXT = "300GB"     # 卡片上印的流量
PRICE_NUMBER = "38"       # 价格（不带单位）
PRICE_TEXT = "38元"       # 价格（页面上怎么写）
REGION_WORDS = ("广州", "上海", "北京")   # 卡片提供的地域，弹窗可能让你选
BUY_MARKERS = ("...", "...")  # 认「这是我们的面板」用的词，**价格一定要留在里面**
```

两点要紧的：

- **`BUY_MARKERS` 里必须有价格。** 没有价格这道门槛，脚本会把页面上别的商品行
  当成购买弹窗——2026-10-26 就是这么丢的单。
- **换商品的话，测试里的样例页面也要跟着改。** `test_snap_target.py` 和
  `test_dialog.py` 里各有一个用上面这些值拼出来的样例页。

---

## 每次跑完都会有一份报告

**成功失败都写。** 每次运行在 `runs\<时间戳>\` 下留下：

| 文件 | 内容 |
| --- | --- |
| `report.md` | 一页纸的结论，给人看的。开头就写着这次为什么成、为什么没成 |
| `report.json` | 同样的内容，机器读的，可以统计多次跑的成败 |
| `*.png` | 出错当时的页面截图（只在失败时才有） |

报告第一行是**判定码**，一个固定字符串：

| 判定码 | 意思 |
| --- | --- |
| `OK_ORDER_CREATED` | 订单已经生成，**该你付钱** |
| `OK_USER_TOOK_OVER` | 脚本试了几次没成交，主动让位，你自己点 |
| `OK_SURVEY_ONLY` | 跑的是 `--survey`，只看不点 |
| `OK_DIALOG_CONFIRMED` | 认出了购买框，也点了确认 |
| `FAIL_NO_CARD` | 页面上没扫到目标那张卡（价格或规格变了） |
| `FAIL_SOLD_OUT` | 按钮自己说这一场卖完了 |
| `FAIL_NO_FLIP` | 一直等，按钮也没翻成「立即抢购」 |
| `FAIL_CLICK_MISSED` | 点了，但按钮还是「立即抢购」 |
| `FAIL_NO_DIALOG` | 点了「立即抢购」，但购买框没出来 |
| `FAIL_DIALOG_WRONG` | 弹出了框，但不是我们要的商品/价格 |
| `FAIL_NO_CONFIRM` | 框认出来了，但框里没有我们敢点的确认键 |
| `FAIL_REGION_MISSING` | 框里要选地域，但配置里的地域一个都没匹配上 |
| `FAIL_ORDER_REJECTED` | 点了确认，但下单接口返回失败 |
| `FAIL_NO_ORDER_REQ` | 点了确认，可页面从头到尾没发下单请求 |
| `FAIL_LOGIN` | 页面跳到登录页了 |
| `FAIL_UNKNOWN` | 结束得不对，但归不了类——把 `report.md` 和截图发出来 |

`report.md` 里还有一张**关键事实表**（场次、act_id、cookie 数量、翻牌→点击用了
多少毫秒、到底有没有发出下单请求）和一条**时间线**。

老日志也能补报告（报告机制是后来才加的）：

```powershell
.\.venv\Scripts\python.exe retro_report.py snap_browser.log --run-id 20261026-150000-lost
```

---

## 命令

```powershell
# 登录（每场活动前）
.\.venv\Scripts\python.exe get_cookies.py

# 自检（本地合成页面，不联网）
.\.venv\Scripts\python.exe test_snap_target.py
.\.venv\Scripts\python.exe test_dialog.py
.\.venv\Scripts\python.exe test_report.py

# 只看看页面上有什么，不点任何东西
.\.venv\Scripts\python.exe snap_browser.py --survey

# 正式抢（提前 5–10 分钟起，窗口别最小化）
.\.venv\Scripts\python.exe snap_browser.py

# 最多等 30 分钟就收工
.\.venv\Scripts\python.exe snap_browser.py --max-wait=30
```

---

## 它是怎么找那张卡的

1. 打开活动页，**带上你保存的 cookie**。
2. 从页面自己的 `__NEXT_DATA__` 里读出秒杀场次表：哪一场在进行、哪一场是下一场、
   每一场几点开始几点结束。**时间表是页面给的，不用你填。**
3. 滚到卖目标商品的那个小节（卡片在滚到之前只是空骨架，扫不到）。
4. 扫出所有提到目标规格的卡片，**按「规格 + 价格」一起打分**，价格对得上的那张
   才是目标。
5. 把目标按钮框成粉色描边——**你能在浏览器里看到它**，肉眼就能确认它挑对了。
6. 每 80 毫秒读一次那个按钮的文字。它从「添加提醒」翻成「立即抢购」是**页面自己**
   干的活（页面每约 13 秒轮询一次状态），脚本不刷新页面。
7. 一翻牌就点它。然后**按内容**认购买弹窗：写着我们的商品、价格对得上、而且框里
   只有一个商品、一个可点的按钮。整屏遮罩不算，**商品行也不算**。
8. 弹窗里要选地域就选一个，然后点确认。
9. 订单生成，浏览器留在收银台，**你付钱**。

第 7 步那几道门槛不是多余的。2026-10-26 那次，页面上「热门 Agent」那一排 109 元的
商品行里也印着 `4核4G3M`，脚本把它当成了购买弹窗，对着一个永远不会有「确认购买」
的框干等了 25 秒，订单就这么没了。事后报告在
`runs/20261026-150000-lost/report.md`。

---

## 几条硬规矩

1. **只认价格对的那张卡。** 页面上好几个规格一样的卡，判定靠「规格 + 价格」两条一起。
2. **点已经框好的那个按钮。** 开抢那零点几秒来不及重新扫页面，中途不换目标。
3. **弹窗按内容认，不按 class 认。** 页面的 class 名会变，内容不会。
4. **付款永远归你。** 脚本不会点「立即支付」「去支付」，也不会碰收银台。
5. **每次运行都写报告。** 成功失败都写，写清楚为什么。

---

## 文件

| 文件 | 作用 |
| --- | --- |
| `snap_config.py` | **改这个**：商品、价格、地域、页面地址 |
| `snap_browser.py` | 抢购主程序。`--survey` 只看不点，`--max-wait=分钟` 限制等待上限 |
| `snap_report.py` | 报告引擎：判定码、关键事实、时间线、截图 |
| `snap_target.py` | 按钮的定位规则，被主程序和测试共用 |
| `get_cookies.py` | 扫码登录，写出 `cookies.json` |
| `retro_report.py` | 把老日志补成一份报告 |
| `check_target.py` | 只读检查：在真实页面上跑一遍定位规则 |
| `check_title.py` | 只读检查：小节标题找得对不对 |
| `test_snap_target.py` | 离线测试：定位规则 |
| `test_dialog.py` | 离线测试：购买弹窗的处理 |
| `test_report.py` | 离线测试：报告和判定码 |
| `cookies.json` | 登录凭据，**保密，别提交**（已在 `.gitignore` 里） |
| `runs/` | 每次运行的报告（已在 `.gitignore` 里） |

---

## 来源

这个项目的思路来自一篇公开的公众号教程：

> **https://mp.weixin.qq.com/s/gXDODqg6Q3vU9WAvionnUQ**

那篇文章讲的是用纯 HTTP 请求直接打下单接口的路子。本仓库**不是**那篇文章的代码，
是**另写的一份实现**：因为现在的下单请求带签名，纯 HTTP 复现不出来，所以这里改成
驱动真实页面、让页面自己签名发请求。文章只作为思路来源和阅读链接列在这里。

如果你要的是纯 HTTP 版本，请直接看原文。

---

**免责**：脚本不保证成功，结果取决于库存、账号资格、网络和服务商风控。
付款前请自己核对订单的规格、地域、镜像、价格和有效期。多个地域同时下单可能产生
多张订单，只留一张。

**风险提示**：自动点击抢购属于自动化操作，请自行确认是否符合服务商的
活动规则；因违反规则导致账号受限，脚本作者不承担任何责任。
