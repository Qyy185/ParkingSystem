"""
自检脚本：验证业务规则 + 服务器能起来、页面能出、表单能提交。

用法（在项目目录下）：
    .venv\\Scripts\\python.exe tools\\selftest.py

分两部分：
  A. 业务规则单元测试（直接调 parking 层，快、不依赖网络）
  B. HTTP 端到端测试（真的起一个服务器，请求页面和表单）
"""

import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 3000
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable

sys.path.insert(0, ROOT)
import account  # noqa: E402
import parking  # noqa: E402
import plate as plate_mod  # noqa: E402
import store  # noqa: E402

results = []


def check(name, ok, extra=""):
    results.append({"name": name, "ok": ok, "extra": extra})


# ============================================================
# A. 业务规则单元测试
# ============================================================

def reset_data():
    """把运行数据复位到 16 个空车位、0 条流水、0 个账户。"""
    store.write_all(parking.init_spots())
    os.makedirs(store.DATA_DIR, exist_ok=True)
    with open(store.LOG_FILE, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)
    store.write_accounts([])


def fee_at(minutes):
    """算「停了 minutes 分钟」时的费用。"""
    entry = store.now_ms() - int(minutes * 60 * 1000)
    return parking.calc_fee(entry)


def test_calc_fee():
    """计费规则：30 分钟免费 → 5 元/小时 → 每天封顶 40 元。"""
    # 关键用例：能区分「先减免费时长再取整」和「直接取整」两种实现
    cases = [
        (10, 0, "免费时段内"),
        (30, 0, "免费时段边界"),
        (45, 5, "超出免费 15 分钟，向上取整为 1 小时"),
        (90, 5, "超出免费 60 分钟，计 1 小时"),
        (120, 10, "超出免费 90 分钟，计 2 小时"),
    ]
    for minutes, expect, why in cases:
        got = fee_at(minutes)
        check("计费 %d 分钟 = %d 元" % (minutes, expect),
              got == expect, "实际 %d（%s）" % (got, why))

    # 每天封顶：停 8 小时正好到封顶
    check("计费 8 小时 = 40 元（当日封顶）",
          fee_at(480) == 40, "实际 %d" % fee_at(480))

    # ★ 跨天必须按天分段累加，这是本次修复的核心
    check("计费 1 天 = 40 元（单日封顶）",
          fee_at(24 * 60) == 40, "实际 %d" % fee_at(24 * 60))
    check("计费 2 天 = 80 元（按天分段，不是总额封顶）",
          fee_at(48 * 60) == 80, "实际 %d" % fee_at(48 * 60))
    check("计费 3 天 = 120 元（按天分段）",
          fee_at(72 * 60) == 120, "实际 %d" % fee_at(72 * 60))


def test_plate_normalize():
    """车牌归一化：大小写、空格、连字符、全角都不影响。"""
    check("归一化 小写→大写",
          plate_mod.normalize("黑a12345") == "黑A12345")
    check("归一化 去内部空格",
          plate_mod.normalize("黑A 12345") == "黑A12345")
    check("归一化 去连字符",
          plate_mod.normalize("黑A-12345") == "黑A12345")
    check("归一化 全角→半角",
          plate_mod.normalize("黑Ａ12345") == "黑A12345")
    check("归一化 去首尾空白",
          plate_mod.normalize("  黑A12345  ") == "黑A12345")
    check("归一化 空输入返回空串",
          plate_mod.normalize("   ") == "")


def test_plate_validate():
    """车牌格式校验：合法的过，非法��拦。"""
    valid = ["黑A12345", "京B12345", "沪C12345", "粤D12345",
             "黑A123456", "京A挂12345"]
    for p in valid:
        check("车牌 %s 格式合法" % p, plate_mod.is_valid(p))

    invalid = ["abc", "123", "黑A123456789012", "A12345",
               "黑A", "", "黑A1234"]
    for p in invalid:
        check("车牌 %r 被拒绝" % p, not plate_mod.is_valid(p))


def test_duplicate_entry():
    """同一辆车不能重复入场、不能占两个车位。"""
    reset_data()
    r1 = parking.handle_entry("黑A12345")
    check("首次入场成功", r1.get("ok") is True, str(r1.get("msg", "")))

    r2 = parking.handle_entry("黑A12345")
    check("重复入场被拒绝",
          r2.get("ok") is False and "已在场内" in r2.get("msg", ""),
          r2.get("msg", ""))

    # 归一化后应视为同一辆车
    r3 = parking.handle_entry("黑a 12345")
    check("大小写/空格不同的同一辆车也被拒绝",
          r3.get("ok") is False and "已在场内" in r3.get("msg", ""),
          r3.get("msg", ""))

    spots = store.read_all()
    occupied = [s for s in spots if s.get("status") == "occupied"]
    check("同一辆车只占 1 个车位",
          len(occupied) == 1, "实际占 %d 个" % len(occupied))


def test_full_flow():
    """完整闭环：入场 → 查费 → 缴费 → 车位释放。"""
    reset_data()
    r = parking.handle_entry("辽B88888")
    check("入场拿到车位", r.get("ok") is True)
    spot_id = r["spot"]["id"]

    e = parking.handle_exit("辽B88888")
    check("出场查到费用", e.get("ok") is True and "fee" in e)

    # 出场不放行
    still = [s for s in store.read_all() if s.get("status") == "occupied"]
    check("出场后车位仍占用（未放行）", len(still) == 1)

    p = parking.handle_pay("辽B88888")
    check("缴费成功", p.get("ok") is True, p.get("msg", ""))

    after = [s for s in store.read_all() if s.get("status") == "occupied"]
    check("缴费后车位已释放", len(after) == 0, "仍占用 %d 个" % len(after))

    # 流水要有入场和缴费两条
    logs = store.read_logs()
    types = [l.get("type") for l in logs]
    check("流水记录了入场", "in" in types, str(types))
    check("流水记录了缴费", "pay" in types, str(types))


def test_exit_unknown_car():
    """不存在的车出场要报错，不能崩。"""
    reset_data()
    r = parking.handle_exit("京Z99999")
    check("不存在的车出场被拒绝",
          r.get("ok") is False and "没找到" in r.get("msg", ""),
          r.get("msg", ""))


# ============================================================
# A2. 账户与分流（v0.2.1）
# ============================================================

def test_account_register():
    """账户登记、查询、改类型、删除。"""
    reset_data()

    r = account.register("黑A12345", account.MONTHLY, "张三", "月租车主")
    check("登记月租账户成功", r.get("ok") is True and r.get("created") is True,
          r.get("msg", ""))
    check("账户车牌已归一化",
          r["account"]["plate"] == "黑A12345", r["account"]["plate"])

    # 归一化后应能查到
    found = account.find_by_plate("黑a 12345")
    check("按归一化车牌能查到账户", found is not None)
    check("查到的是月租类型",
          found is not None and found["kind"] == account.MONTHLY)

    # 重复登记 = 更新，不是新建
    r2 = account.register("黑A12345", account.TEMPORARY)
    check("重复登记是更新不是新建",
          r2.get("ok") is True and r2.get("created") is False)
    check("账户总数仍为 1", len(account.all_accounts()) == 1,
          "实际 %d" % len(account.all_accounts()))

    # 非法车牌不能登记
    r3 = account.register("abc", account.TEMPORARY)
    check("非法车牌不能登记账户",
          r3.get("ok") is False, r3.get("msg", ""))

    # 改类型
    r4 = account.change_kind("黑A12345", account.TEMPORARY)
    check("账户类型可切换",
          r4.get("ok") is True and r4["account"]["kind"] == account.TEMPORARY)

    # 删除
    r5 = account.remove("黑A12345")
    check("账户可删除", r5.get("ok") is True)
    check("删除后账户数为 0", len(account.all_accounts()) == 0)


def test_classify():
    """分流判断：查账户定月租/临停，没登记的按临停。"""
    reset_data()
    account.register("黑A11111", account.MONTHLY)
    account.register("黑A22222", account.TEMPORARY)

    kind, acc = account.classify("黑A11111")
    check("月租账户分流为 monthly", kind == account.MONTHLY, kind)
    check("月租账户能取到账户对象", acc is not None)

    kind, _ = account.classify("黑A22222")
    check("临停账户分流为 temporary", kind == account.TEMPORARY, kind)

    kind, acc = account.classify("黑A99999")
    check("未登记车牌按临停处理", kind == account.TEMPORARY, kind)
    check("未登记车牌没有账户对象", acc is None)


def test_entry_channel_split():
    """★ 核心：入场按【车牌】分流，不是按车位。"""
    reset_data()
    account.register("黑A11111", account.MONTHLY, "月租车主")
    account.register("黑A22222", account.TEMPORARY, "临停车主")

    r1 = parking.handle_entry("黑A11111")
    check("月租车入场成功", r1.get("ok") is True)
    check("月租车走 monthly 通道",
          r1.get("channel") == account.MONTHLY, str(r1.get("channel")))

    r2 = parking.handle_entry("黑A22222")
    check("临停车入场成功", r2.get("ok") is True)
    check("临停车走 temp 通道",
          r2.get("channel") == "temp", str(r2.get("channel")))

    r3 = parking.handle_entry("黑A99999")
    check("未登记车入场成功（不拒绝陌生人）", r3.get("ok") is True)
    check("未登记车走 temp 通道", r3.get("channel") == "temp", str(r3.get("channel")))

    # 车位上要记下通道
    spots = store.read_all()
    occupied = [s for s in spots if s.get("status") == "occupied"]
    check("在场的车有 3 辆", len(occupied) == 3, "实际 %d" % len(occupied))
    monthly_spots = [s for s in occupied if s.get("channel") == account.MONTHLY]
    check("其中 1 辆是月租通道", len(monthly_spots) == 1, "实际 %d" % len(monthly_spots))


def test_monthly_not_charged():
    """★ 月租车出场费用必须为 0。"""
    reset_data()
    account.register("黑A11111", account.MONTHLY)

    parking.handle_entry("黑A11111")
    # 把入场时间往前挪 3 天，模拟长期停放
    spots = store.read_all()
    for s in spots:
        if s.get("plate") == "黑A11111":
            s["entryTime"] = store.now_ms() - 3 * 24 * 60 * 60 * 1000
    store.write_all(spots)

    e = parking.handle_exit("黑A11111")
    check("月租车出场查到费用", e.get("ok") is True)
    check("月租车停 3 天费用为 0 元", e.get("fee") == 0,
          "实际 %d 元" % e.get("fee", -1))
    check("月租车出场通道是 monthly",
          e.get("channel") == account.MONTHLY, str(e.get("channel")))

    p = parking.handle_pay("黑A11111")
    check("月租车缴费成功", p.get("ok") is True)
    check("月租车缴费金额为 0", p.get("fee") == 0)

    # 车位要释放
    still = [s for s in store.read_all() if s.get("status") == "occupied"]
    check("月租车缴费后车位已释放", len(still) == 0, "仍占用 %d 个" % len(still))


def test_temp_still_charged():
    """★ 临停车照常计费，不能被月租逻辑误伤。"""
    reset_data()
    account.register("黑A22222", account.TEMPORARY)

    parking.handle_entry("黑A22222")
    spots = store.read_all()
    for s in spots:
        if s.get("plate") == "黑A22222":
            s["entryTime"] = store.now_ms() - 2 * 60 * 60 * 1000
    store.write_all(spots)

    e = parking.handle_exit("黑A22222")
    check("临停车停 2 小时费用为 10 元", e.get("fee") == 10,
          "实际 %d 元" % e.get("fee", -1))
    check("临停车通道是 temp", e.get("channel") == "temp", str(e.get("channel")))


def test_account_kind_switch_affects_entry():
    """账户类型改了，下次入场就要走新通道。"""
    reset_data()
    account.register("黑A11111", account.MONTHLY)

    r1 = parking.handle_entry("黑A11111")
    check("初始按月租入场", r1.get("channel") == account.MONTHLY)

    parking.handle_pay("黑A11111")
    account.change_kind("黑A11111", account.TEMPORARY)

    r2 = parking.handle_entry("黑A11111")
    check("改成临停后按临停入场", r2.get("channel") == "temp",
          str(r2.get("channel")))


def test_account_not_touched_by_payment():
    """★ 账户不沾付款：缴费不会改账户，账户里也没有金额字段。"""
    reset_data()
    account.register("黑A11111", account.MONTHLY, "张三")
    parking.handle_entry("黑A11111")
    parking.handle_pay("黑A11111")

    acc = account.find_by_plate("黑A11111")
    check("缴费后账户还在", acc is not None)
    check("账户没有余额字段", "balance" not in acc)
    check("账户没有金额字段", "amount" not in acc and "fee" not in acc)
    check("账户类型没被缴费改动", acc["kind"] == account.MONTHLY)


# ============================================================
# B. HTTP 端到端测试
# ============================================================

def get(path):
    """GET 一个页面，返回 (状态码, 文本)。"""
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def post(path, data):
    """POST 表单。返回 (最终状态码, 最终 URL, 文本)。

    表单提交后会 302 跳转到带提示参数的地址，
    所以这里要看最终 URL 里有没有 msg= / ok= 参数。
    """
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (PORT, path),
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.geturl(), resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.geturl(), e.read().decode("utf-8")


def test_http():
    print("=== 启动服务器 ===")
    proc = subprocess.Popen(
        [PY, os.path.join(ROOT, "app.py")],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    time.sleep(2.5)

    try:
        # 1. 车主端页面
        status, text = get("/")
        check("GET / 200", status == 200, "status=%d" % status)
        check("车主端含标题", "地下停车场" in text)
        cells = re.findall(r'class="cell ', text)
        check("渲染出 16 个车位", len(cells) == 16, "实际 %d" % len(cells))
        check("含入场表单", 'action="/entry"' in text)
        check("含出场表单", 'action="/exit"' in text)

        # 2. 管理后台
        status, text = get("/admin")
        check("GET /admin 200", status == 200, "status=%d" % status)
        check("后台含流水表", "进出场流水" in text)

        # 3. 静态资源
        status, _ = get("/static/style.css")
        check("GET /static/style.css 200", status == 200, "status=%d" % status)

        # 4. 空车牌被拦
        status, url, text = post("/entry", {"plate": ""})
        check("POST /entry 空车牌被拦下",
              status == 200 and "%E4%B8%8D%E8%83%BD%E4%B8%BA%E7%A9%BA" in url
              or "不能为空" in url, url)

        # 5. 非法车牌被拦
        status, url, text = post("/entry", {"plate": "abc"})
        check("POST /entry 非法车牌被拦下",
              status == 200 and "msg=" in url, url)

        # 6. 合法车牌入场成功
        status, url, text = post("/entry", {"plate": "黑A12345"})
        check("POST /entry 合法车牌入场成功",
              status == 200 and "ok_msg=" in url, url)

        # 7. 同一车牌再入场，HTTP 层也要拦住
        status, url, text = post("/entry", {"plate": "黑A12345"})
        check("POST /entry 重复入场被拦下",
              status == 200 and "msg=" in url, url)

        # 8. 提示信息真的渲染到页面上了
        status, text = get("/?msg=%E6%B5%8B%E8%AF%95%E6%8F%90%E7%A4%BA")
        check("提示信息渲染到页面", "测试提示" in text)

        # 9. 后台流水时间显示的是可读时间，不是毫秒数字
        status, text = get("/admin")
        check("后台流水时间已格式化为可读时间",
              re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text) is not None)

        # 10. 扫码页能打开
        status, text = get("/scan")
        check("GET /scan 200", status == 200, "status=%d" % status)
        check("扫码页含取景框", "scan-view" in text)
        check("扫码页含模拟扫描按钮", "模拟扫描" in text)

        # 11. 模拟扫描 → 识别输入框
        status, url, text = post("/scan/detect", {})
        check("POST /scan/detect 进入识别状态",
              status == 200 and "识别中" in text)

        # 12. 扫码确认入场（未登记 → 临停通道）
        status, url, text = post("/scan/confirm", {"plate": "黑A55555"})
        check("扫码确认入场成功（未登记走临停）",
              status == 200 and "ok=" in url, url)

        # 13. 账户管理页能打开
        status, text = get("/accounts")
        check("GET /accounts 200", status == 200, "status=%d" % status)
        check("账户页含新增表单", 'action="/accounts/add"' in text)

        # 14. 登记账户 → 列表里能看到
        status, url, text = post("/accounts/add",
                                  {"plate": "黑A66666", "kind": "monthly",
                                   "holder": "测试", "note": ""})
        check("POST /accounts/add 登记成功",
              status == 200 and "ok=" in url, url)
        status, text = get("/accounts")
        check("账户列表里能看到新账户", "黑A66666" in text)
        check("账户列表显示月租标签", "kind-badge monthly" in text)

        # 15. 切换账户类型
        status, url, text = post("/accounts/toggle",
                                  {"plate": "黑A66666", "kind": "temporary"})
        check("POST /accounts/toggle 切换成功",
              status == 200 and "ok=" in url, url)

        # 16. 删除账户
        status, url, text = post("/accounts/delete", {"plate": "黑A66666"})
        check("POST /accounts/delete 删除成功",
              status == 200 and "ok=" in url, url)

        # 17. 404
        status, _ = get("/no-such-page")
        check("不存在的页面返回 404", status == 404, "status=%d" % status)

    except Exception as e:
        check("请求过程无异常", False, str(e))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def main():
    print("=== 业务规则单元测试 ===")
    test_calc_fee()
    test_plate_normalize()
    test_plate_validate()
    test_duplicate_entry()
    test_full_flow()
    test_exit_unknown_car()
    test_account_register()
    test_classify()
    test_entry_channel_split()
    test_monthly_not_charged()
    test_temp_still_charged()
    test_account_kind_switch_affects_entry()
    test_account_not_touched_by_payment()

    test_http()

    reset_data()
    print()
    print("=== 自检结果 ===")
    passed = 0
    for r in results:
        print("[%s] %s%s" % ("PASS" if r["ok"] else "FAIL", r["name"],
                              ("  (" + r["extra"] + ")") if r["extra"] else ""))
        if r["ok"]:
            passed += 1
    print("---")
    print("%d / %d 通过" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
