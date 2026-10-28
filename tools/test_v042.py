#!/usr/bin/env python3
"""
v0.4.2 端到端测试：车位 64 个 + 权限分离 + 模拟支付确认页。

起一个真的 Flask 服务（用 werkzeug 的 make_server，不阻塞），
然后用 urllib 模拟浏览器走完整流程。

用法：
    python tools/test_v042.py
"""

import http.cookiejar
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------
# 准备：临时数据目录，别污染项目的 data/
# ---------------------------------------------------------
TMP = tempfile.mkdtemp(prefix="parking_v042_")
os.environ["ACCESS_PWD"] = "parking2026"
os.environ["ADMIN_PWD"] = "admin2026"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["PORT"] = "3199"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import store  # noqa: E402

# 把数据目录指到临时目录
_orig_read = store.read_all
_orig_write = store.write_all
_orig_logs = store.read_logs
_orig_addlog = store.add_log


def _p(name):
    return os.path.join(TMP, name)


def read_all():
    p = _p("parking.json")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_all(spots):
    with open(_p("parking.json"), "w", encoding="utf-8") as f:
        json.dump(spots, f, ensure_ascii=False, indent=2)


def read_logs():
    p = _p("logs.json")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def add_log(item):
    logs = read_logs()
    item.setdefault("time", store.now_ms())
    logs.append(item)
    with open(_p("logs.json"), "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


def _read_json(name, default):
    p = _p(name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _write_json(name, data):
    with open(_p(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ★ accounts / recharges / notifications 也要隔离 ——
#   只隔离 parking.json 的话，测试会给**项目真实的** data/accounts.json
#   写进一堆测试账户（余额 10800 这种），把演示数据搞脏。
#   （2026-10-28 踩过：跑完测试 data/accounts.json 里多了两个账户）
def read_accounts():
    return _read_json("accounts.json", [])


def write_accounts(data):
    _write_json("accounts.json", data)


def read_recharges():
    return _read_json("recharges.json", [])


def write_recharges(data):
    _write_json("recharges.json", data)


def read_notifications():
    return _read_json("notifications.json", [])


def write_notifications(data):
    _write_json("notifications.json", data)


store.read_all = read_all
store.write_all = write_all
store.read_logs = read_logs
store.add_log = add_log
store.read_accounts = read_accounts
store.write_accounts = write_accounts
store.read_recharges = read_recharges
store.write_recharges = write_recharges
store.read_notifications = read_notifications
store.write_notifications = write_notifications

import account  # noqa: E402
import app as appmod  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s%s" % (name, ("  " + extra) if extra else ""))
    else:
        FAIL += 1
        print("[FAIL] %s%s" % (name, ("  " + extra) if extra else ""))


def text_of(html):
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# ---------------------------------------------------------
# 起服务
# ---------------------------------------------------------
from werkzeug.serving import make_server  # noqa: E402

server = make_server("127.0.0.1", 3199, appmod.app, threaded=True)
import threading  # noqa: E402

threading.Thread(target=server.serve_forever, daemon=True).start()

BASE = "http://127.0.0.1:3199"


class Browser:
    """模拟一个浏览器（带 cookie jar）。"""

    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj)
        )
        # 不自动跟随重定向，这样才能看到 302 去哪
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        self.noredir = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj), NoRedirect
        )

    def get(self, path, follow=True):
        """follow=False 时不自动跟随 302 —— 看真实状态码要用这个。

        ★ 踩过的坑：自动跟随后 302 会变成 200（跳到 /access 页面），
          断言「被拦」时看起来像没拦住，其实是拦了。
        """
        op = self.op if follow else self.noredir
        try:
            r = op.open(BASE + path, timeout=15)
            return r.status, r.geturl(), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location", ""), e.read().decode("utf-8", "replace")

    def post(self, path, data, follow=True):
        body = urllib.parse.urlencode(data).encode()
        op = self.op if follow else self.noredir
        try:
            r = op.open(BASE + path, body, timeout=15)
            return r.status, r.geturl(), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location", ""), e.read().decode("utf-8", "replace")

    def role(self):
        for c in self.cj:
            if c.name == "session":
                return c.value
        return ""


print("=" * 60)
print("v0.4.2 端到端测试")
print("=" * 60)

# =========================================================
# 1. 车位：64 个、无分区
# =========================================================
print("\n--- 1. 车位 64 个 / 无分区 ---")
import parking  # noqa: E402

spots = parking.get_spots()
check("车位总数是 64", len(spots) == 64, "实际 %d" % len(spots))
check("没有 zone 字段", not any("zone" in s for s in spots))
check("第一个车位 P-01", spots[0]["id"] == "P-01", spots[0]["id"])
check("最后一个车位 P-64", spots[-1]["id"] == "P-64", spots[-1]["id"])

# =========================================================
# 2. 权限分离
# =========================================================
print("\n--- 2. 权限分离 ---")

anon = Browser()
st, loc, _ = anon.get("/admin", follow=False)
check("未登录访问 /admin 被拦", st == 302, "HTTP %s" % st)
check("跳到管理端口令页", "role=admin" in loc, loc)

st, loc, _ = anon.get("/", follow=False)
check("未登录访问车主端被拦", st == 302, "HTTP %s" % st)

# 车主登录
owner = Browser()
st, url, body = owner.post("/access?role=owner", {"pwd": "parking2026", "role": "owner"})
check("车主口令正确能登录", st == 200, "HTTP %s" % st)
check("登录后跳回车主端", url.endswith("/"), url)

# 车主能不能进后台？—— 不能
st, loc, _ = owner.get("/admin", follow=False)
check("★ 车主访问 /admin 被拒", st == 302, "HTTP %s" % st)
st2, _, body2 = owner.get("/admin", follow=True)
check("★ 车主访问 /admin 时提示权限不足",
      "权限不足" in body2 or "role=admin" in body2, body2[:120])

# 车主能不能进账户管理？—— 不能
st, loc, _ = owner.get("/accounts", follow=False)
check("★ 车主访问 /accounts 被拒", st == 302, "HTTP %s" % st)

# 车主能不能进催缴？—— 不能
st, loc, _ = owner.get("/remind", follow=False)
check("★ 车主访问 /remind 被拒", st == 302, "HTTP %s" % st)

# 车主能不能进车主端？—— 能
st, _, body = owner.get("/")
check("车主能进车主端", st == 200, "HTTP %s" % st)
check("★ 车主端不显示管理后台链接", "管理后台" not in body)
check("★ 车主端不显示账户管理链接", "账户管理" not in body)
check("★ 车主端不显示催缴链接", "催缴" not in body)

# 错误口令
bad = Browser()
st, url, body = bad.post("/access?role=owner", {"pwd": "wrongpwd", "role": "owner"})
check("错误车主口令被拒", "访问口令不对" in body, body[:80])

# 管理员登录
admin = Browser()
st, url, body = admin.post("/access?role=admin", {"pwd": "admin2026", "role": "admin"})
check("管理员口令正确能登录", st == 200, "HTTP %s" % st)
check("登录后跳到管理后台", "/admin" in url, url)

st, _, body = admin.get("/admin")
check("管理员能进管理后台", st == 200, "HTTP %s" % st)
check("★ 后台显示管理员徽章", "管理员" in body)

st, _, body = admin.get("/accounts")
check("管理员能进账户管理", st == 200, "HTTP %s" % st)

st, _, body = admin.get("/remind")
check("管理员能进催缴", st == 200, "HTTP %s" % st)

st, _, body = admin.get("/")
check("管理员也能进车主端", st == 200, "HTTP %s" % st)

# 管理员口令不能当车主口令用
mix = Browser()
st, url, body = mix.post("/access?role=owner", {"pwd": "admin2026", "role": "owner"})
check("★ 管理员口令不能登录车主端", "访问口令不对" in body, body[:80])

# 车主口令不能当管理员口令用
mix2 = Browser()
st, url, body = mix2.post("/access?role=admin", {"pwd": "parking2026", "role": "admin"})
check("★ 车主口令不能登录管理后台", "管理员口令不对" in body, body[:80])

# =========================================================
# 3. 车位展示：只有统计
# =========================================================
print("\n--- 3. 车位只显示统计 ---")
st, _, body = owner.get("/")
check("车主端显示总车位 64", "64" in body)
check("车主端显示「已占用」", "已占用" in body)
check("车主端显示「空闲」", "空闲" in body)
check("★ 不再逐个渲染车位格子", "cell-id" not in body, "找到 cell-id")
check("★ 不显示车位编号 P-01", "P-01" not in body)

st, _, body = admin.get("/admin")
check("后台显示总车位 64", "64" in body)
check("★ 后台不再逐个渲染车位格子", "cell-id" not in body)

# =========================================================
# 4. 充值确认页
# =========================================================
print("\n--- 4. 充值走模拟支付确认页 ---")
owner2 = Browser()
owner2.post("/access?role=owner", {"pwd": "parking2026", "role": "owner"})

# 选套餐 + 时长 → 应该跳到确认页
st, url, body = owner2.post("/recharge/select",
                             {"plate": "黑A66666", "kind": "yearly", "periods": "1"})
check("★ 选完档位跳到模拟支付页", "/pay/confirm" in url, url)
check("确认页显示金额 2700", "2700" in body)
check("★ 确认页有「确认支付」按钮", "确认支付" in body)
check("★ 确认页有支付方式选择", "微信支付" in body and "支付宝" in body)
check("★ 确认页标明是模拟收银台", "模拟" in body)

# 此时还没扣款
acc = account.find_by_plate("黑A66666")
check("★ 确认页阶段还没扣款", acc is None, "账户已存在: %s" % acc)

# 点确认支付 → 应该真扣款
st, url, body = owner2.post("/pay/confirm?kind=recharge",
                             {"kind": "recharge", "method": "wechat"})
acc = account.find_by_plate("黑A66666")
check("★ 确认支付后账户已开通", acc is not None)
if acc:
    check("★ 余额 2700 元", acc.get("balance") == 2700, "实际 %s" % acc.get("balance"))
check("★ 提示里有支付方式", "微信支付" in urllib.parse.unquote(url), urllib.parse.unquote(url)[:150])

# 重复提交会不会重复扣款？（session 已清空）
st, url, body = owner2.post("/pay/confirm?kind=recharge",
                             {"kind": "recharge", "method": "wechat"})
acc2 = account.find_by_plate("黑A66666")
check("★ 重复提交不会重复扣款",
      acc2 and acc2.get("balance") == 2700,
      "余额 %s" % (acc2.get("balance") if acc2 else None))

# =========================================================
# 5. 付款确认页
# =========================================================
print("\n--- 5. 缴费走模拟支付确认页 ---")
owner3 = Browser()
owner3.post("/access?role=owner", {"pwd": "parking2026", "role": "owner"})

# 入场
st, url, body = owner3.post("/entry", {"plate": "黑A12345", "action": "entry"})
# ★ 入场成功消息在 URL 的 ok_msg 参数里，不在 HTML 正文 ——
#   redirect(url_for("index", ok_msg="...")) 靠 query string 传提示
_t = urllib.parse.unquote(url)
check("入场成功", "入场成功" in _t, _t[:200])
check("入场消息含车位号", "P-" in _t, _t[:200])
check("★ 入场消息不含分区字样", "A区" not in _t and "B区" not in _t, _t[:200])

# 查询费用 → 应该跳确认页
st, url, body = owner3.post("/exit", {"plate": "黑A12345"})
check("★ 查询费用跳到模拟支付页", "/pay/confirm" in url, url)
check("确认页显示车牌", "黑A12345" in body)

# 车位还没释放
spots = parking.get_spots()
still = [s for s in spots if s.get("status") == "occupied" and s.get("plate") == "黑A12345"]
check("★ 确认页阶段车位还没释放", len(still) == 1, "占用 %d 个" % len(still))

# 确认支付 → 车位释放
st, url, body = owner3.post("/pay/confirm?kind=parking",
                             {"kind": "parking", "method": "alipay"})
spots = parking.get_spots()
still = [s for s in spots if s.get("status") == "occupied" and s.get("plate") == "黑A12345"]
check("★ 确认支付后车位已释放", len(still) == 0, "仍占用 %d 个" % len(still))
check("★ 提示里有支付方式", "支付宝" in urllib.parse.unquote(url), urllib.parse.unquote(url)[:150])

# =========================================================
# 6. 金额不能被篡改
# =========================================================
print("\n--- 6. 金额防篡改 ---")
owner4 = Browser()
owner4.post("/access?role=owner", {"pwd": "parking2026", "role": "owner"})
owner4.post("/recharge/select", {"plate": "黑A77777", "kind": "yearly", "periods": "1"})
# 试图用 periods=999 骗系统（应该按 session 里的 periods 算）
st, url, body = owner4.post("/pay/confirm?kind=recharge",
                             {"kind": "recharge", "method": "wechat", "periods": "999"})
acc3 = account.find_by_plate("黑A77777")
check("★ 改 periods 不会算错金额",
      acc3 is None or acc3.get("balance") == 2700,
      "余额 %s" % (acc3.get("balance") if acc3 else None))

# =========================================================
# 7. 退出登录
# =========================================================
print("\n--- 7. 退出登录 ---")
# ★ 用 follow=False：跟随后拿到的是 /access 的 200，看不出真实跳转
st, loc, _ = admin.get("/logout", follow=False)
check("退出后跳回口令页", "access" in loc, loc)
st, loc, _ = admin.get("/admin", follow=False)
check("★ 退出后进不了后台", st == 302, "HTTP %s" % st)

# =========================================================
print("\n" + "=" * 60)
print("%d / %d 通过" % (PASS, PASS + FAIL))
print("=" * 60)

server.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if FAIL == 0 else 1)
