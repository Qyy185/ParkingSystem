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
import tempfile
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
    """把运行数据复位到 16 个空车位、0 条流水、0 个账户、0 条充值记录。"""
    store.write_all(parking.init_spots())
    os.makedirs(store.DATA_DIR, exist_ok=True)
    with open(store.LOG_FILE, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)
    store.write_accounts([])
    store.write_recharges([])


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
    r2 = account.register("黑A12345", account.YEARLY)
    check("重复登记是更新不是新建",
          r2.get("ok") is True and r2.get("created") is False)
    check("账户总数仍为 1", len(account.all_accounts()) == 1,
          "实际 %d" % len(account.all_accounts()))

    # 非法车牌不能登记
    r3 = account.register("abc", account.MONTHLY)
    check("非法车牌不能登记账户",
          r3.get("ok") is False, r3.get("msg", ""))

    # 临停不是长期账户类型
    r4 = account.register("黑A12345", account.TEMPORARY)
    check("长期账户不能登记成临停",
          r4.get("ok") is False, r4.get("msg", ""))

    # 改类型
    r5 = account.change_kind("黑A12345", account.MONTHLY)
    check("账户类型可切换",
          r5.get("ok") is True and r5["account"]["kind"] == account.MONTHLY)

    # 删除
    r6 = account.remove("黑A12345")
    check("账户可删除", r6.get("ok") is True)
    check("删除后账户数为 0", len(account.all_accounts()) == 0)


def test_classify():
    """分流判断：查账户定月租/年租/临停，没登记的按临停。"""
    reset_data()
    account.register("黑A11111", account.MONTHLY)
    account.register("黑A22222", account.YEARLY)

    kind, acc = account.classify("黑A11111")
    check("月租账户分流为 monthly", kind == account.MONTHLY, kind)
    check("月租账户能取到账户对象", acc is not None)

    kind, _ = account.classify("黑A22222")
    check("年租账户分流为 yearly", kind == account.YEARLY, kind)

    kind, acc = account.classify("黑A99999")
    check("未登记车牌按临停处理", kind == account.TEMPORARY, kind)
    check("未登记车牌没有账户对象", acc is None)


def test_entry_channel_split():
    """★ 核心：入场按【车牌】分流，不是按车位。"""
    reset_data()
    account.register("黑A11111", account.MONTHLY, "月租车主")
    account.register("黑A22222", account.YEARLY, "年租车主")

    r1 = parking.handle_entry("黑A11111")
    check("月租车入场成功", r1.get("ok") is True)
    check("月租车走 member 通道",
          r1.get("channel") == "member", str(r1.get("channel")))

    r2 = parking.handle_entry("黑A22222")
    check("年租车入场成功", r2.get("ok") is True)
    check("年租车走 member 通道",
          r2.get("channel") == "member", str(r2.get("channel")))

    r3 = parking.handle_entry("黑A99999")
    check("未登记车入场成功（不拒绝陌生人）", r3.get("ok") is True)
    check("未登记车走 temp 通道", r3.get("channel") == "temp", str(r3.get("channel")))

    # 车位上要记下通道
    spots = store.read_all()
    occupied = [s for s in spots if s.get("status") == "occupied"]
    check("在场的车有 3 辆", len(occupied) == 3, "实际 %d" % len(occupied))
    member_spots = [s for s in occupied if s.get("channel") == "member"]
    check("其中 2 辆是会员通道", len(member_spots) == 2, "实际 %d" % len(member_spots))


def test_monthly_not_charged():
    """★ 会员车出场费用必须为 0。"""
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
    check("会员车出场查到费用", e.get("ok") is True)
    check("会员车停 3 天费用为 0 元", e.get("fee") == 0,
          "实际 %d 元" % e.get("fee", -1))
    check("会员车出场通道是 member",
          e.get("channel") == "member", str(e.get("channel")))

    p = parking.handle_pay("黑A11111")
    check("会员车缴费成功", p.get("ok") is True)
    check("会员车缴费金额为 0", p.get("fee") == 0)

    # 车位要释放
    still = [s for s in store.read_all() if s.get("status") == "occupied"]
    check("会员车缴费后车位已释放", len(still) == 0, "仍占用 %d 个" % len(still))


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
    check("初始按月租入场", r1.get("channel") == "member", str(r1.get("channel")))

    parking.handle_pay("黑A11111")
    account.change_kind("黑A11111", account.YEARLY)

    r2 = parking.handle_entry("黑A11111")
    check("改成年租后走 member 通道", r2.get("channel") == "member",
          str(r2.get("channel")))
    check("改成年租后 kind 是 yearly", r2.get("kind") == account.YEARLY,
          str(r2.get("kind")))


def test_account_not_touched_by_payment():
    """★ 账户和付款是两条线：停车费缴费不改账户的会员属性。

    账户里的 balance 是「充值余额」，不是停车费。
    缴停车费不该把余额扣掉（那是 v0.3 的抵扣功能），也不该改账户类型。
    """
    reset_data()
    account.recharge("黑A11111", account.MONTHLY, 1)
    before = account.find_by_plate("黑A11111")

    parking.handle_entry("黑A11111")
    parking.handle_pay("黑A11111")

    after = account.find_by_plate("黑A11111")
    check("缴费后账户还在", after is not None)
    check("账户类型没被缴费改动", after["kind"] == account.MONTHLY)
    check("账户余额没被停车费扣走",
          after.get("balance") == before.get("balance"),
          "%s vs %s" % (after.get("balance"), before.get("balance")))
    check("账户没有停车费字段",
          "fee" not in after and "amount" not in after)


# ============================================================
# A3. 临时账号（v0.2.2）
# ============================================================

def test_temp_account_lifecycle():
    """★ 临时账号：入场自动建，出场自动删。"""
    reset_data()

    # 入场前：没有账户
    check("入场前该车没有账户", account.find_by_plate("黑A55555") is None)

    r = parking.handle_entry("黑A55555")
    check("临停车入场成功", r.get("ok") is True)
    check("临停车走 temp 通道", r.get("channel") == "temp", str(r.get("channel")))
    check("入场时自动建了临时账号", r.get("temp_created") is True)

    acc = account.find_by_plate("黑A55555")
    check("临时账号已存在", acc is not None)
    check("临时账号类型是 temporary",
          acc is not None and acc["kind"] == account.TEMPORARY)
    check("临时账号没有有效期",
          acc is not None and not acc.get("expiresAt"))

    # 出场缴费 → 临时账号被删
    p = parking.handle_pay("黑A55555")
    check("缴费成功", p.get("ok") is True)
    check("出场时删掉了临时账号", p.get("temp_removed") is not None)
    check("出场后该车没有账户了", account.find_by_plate("黑A55555") is None)


def test_temp_account_not_created_for_member():
    """★ 会员入场不能被临时账号覆盖。"""
    reset_data()
    account.register("黑A11111", account.MONTHLY, "张月租")

    r = parking.handle_entry("黑A11111")
    check("会员入场走 member 通道", r.get("channel") == "member", str(r.get("channel")))
    check("会员入场不建临时账号", r.get("temp_created") is False)

    acc = account.find_by_plate("黑A11111")
    check("会员账户类型没变", acc is not None and acc["kind"] == account.MONTHLY)

    # 会员出场后账户保留
    parking.handle_pay("黑A11111")
    acc2 = account.find_by_plate("黑A11111")
    check("会员出场后账户仍在", acc2 is not None)
    check("会员出场后类型仍是月租",
          acc2 is not None and acc2["kind"] == account.MONTHLY)


def test_temp_account_only_one():
    """同一辆车重复入场不该建两个临时账号。"""
    reset_data()
    parking.handle_entry("黑A55555")
    check("第一次入场建了 1 个临时账号", len(account.all_accounts()) == 1,
          "实际 %d" % len(account.all_accounts()))

    # 再入场会被「已在场内」拦住
    r = parking.handle_entry("黑A55555")
    check("重复入场被拒绝", r.get("ok") is False)
    check("重复入场没多建账号", len(account.all_accounts()) == 1,
          "实际 %d" % len(account.all_accounts()))


def test_temp_account_upgrade_to_member():
    """临停转会员：充值时把临时账号升级，不新建。"""
    reset_data()
    parking.handle_entry("黑A55555")
    check("入场后有临时账号", len(account.all_accounts()) == 1)

    r = account.recharge("黑A55555", account.MONTHLY, 1)
    check("临停车充值成功", r.get("ok") is True, r.get("msg", ""))
    check("充值后仍是 1 个账户", len(account.all_accounts()) == 1,
          "实际 %d" % len(account.all_accounts()))

    acc = account.find_by_plate("黑A55555")
    check("临时账号已升级为月租", acc is not None and acc["kind"] == account.MONTHLY)
    check("升级后有余额", acc is not None and acc.get("balance") == 300,
          "实际 %s" % (acc.get("balance") if acc else None))


# ============================================================
# A4. 充值（v0.2.2）
# ============================================================

def test_packages():
    """套餐表：月租 4 档，年租 1 档。"""
    check("有月租套餐", account.MONTHLY in account.PACKAGES)
    check("有年租套餐", account.YEARLY in account.PACKAGES)
    check("月租有 4 个档位", len(account.PACKAGES[account.MONTHLY]["options"]) == 4)
    check("年租有 1 个档位", len(account.PACKAGES[account.YEARLY]["options"]) == 1)

    # 档位价格：买多应该更便宜（折合每月递减）
    opts = account.PACKAGES[account.MONTHLY]["options"]
    per_month = [o["price"] / o["periods"] for o in opts]
    check("月租折合单价随档位递减",
          all(per_month[i] > per_month[i + 1] for i in range(len(per_month) - 1)),
          str([round(p, 1) for p in per_month]))

    # 年租比 12 个月月租更划算
    m12 = [o for o in opts if o["periods"] == 12][0]["price"]
    y1 = account.PACKAGES[account.YEARLY]["options"][0]["price"]
    check("年租比 12 个月月租便宜", y1 < m12, "年租 %d，月租12个月 %d" % (y1, m12))


def test_recharge():
    """充值：加余额、延长期限、记流水。"""
    reset_data()

    r = account.recharge("黑A11111", account.MONTHLY, 1)
    check("充值月租成功", r.get("ok") is True, r.get("msg", ""))
    check("充值金额 300 元", r.get("amount") == 300, str(r.get("amount")))
    check("充值增加 30 天", r.get("days") == 30, str(r.get("days")))

    acc = account.find_by_plate("黑A11111")
    check("账户已开户", acc is not None)
    check("余额 300 元", acc is not None and acc.get("balance") == 300,
          str(acc.get("balance") if acc else None))
    check("有效期约 30 天后",
          acc is not None and 29 * account.DAY < (acc.get("expiresAt", 0) - store.now_ms()) <= 31 * account.DAY)

    # 再充一次，余额累加、有效期顺延
    r2 = account.recharge("黑A11111", account.MONTHLY, 3)
    check("二次充值成功", r2.get("ok") is True)
    acc2 = account.find_by_plate("黑A11111")
    check("余额累加到 1150 元", acc2.get("balance") == 1150,
          str(acc2.get("balance")))
    check("有效期顺延",
          acc2.get("expiresAt") > acc.get("expiresAt"),
          "%s vs %s" % (acc2.get("expiresAt"), acc.get("expiresAt")))

    # 充值记录留痕
    recs = store.read_recharges()
    check("充值记录有 2 条", len(recs) == 2, "实际 %d" % len(recs))
    check("充值记录含车牌", recs[0].get("plate") == "黑A11111")
    check("充值记录含金额", recs[0].get("amount") == 300)

    # 非法车牌 / 非法套餐
    check("非法车牌不能充值",
          account.recharge("abc", account.MONTHLY, 1).get("ok") is False)
    check("临停类型不能充值",
          account.recharge("黑A11111", account.TEMPORARY, 1).get("ok") is False)
    check("不存在的档位不能充值",
          account.recharge("黑A11111", account.MONTHLY, 99).get("ok") is False)


def test_recharge_yearly():
    """年租充值。"""
    reset_data()
    r = account.recharge("黑A22222", account.YEARLY, 1)
    check("年租充值成功", r.get("ok") is True, r.get("msg", ""))
    check("年租金额 2700 元", r.get("amount") == 2700, str(r.get("amount")))
    check("年租增加 365 天", r.get("days") == 365, str(r.get("days")))

    acc = account.find_by_plate("黑A22222")
    check("年租账户类型正确", acc is not None and acc["kind"] == account.YEARLY)


def test_expired_member_downgrades():
    """★ 会员过期后自动降级成临停，照常计费。"""
    reset_data()
    account.register("黑A11111", account.MONTHLY, "张月租")

    # 把有效期设成过去
    accounts_list = account.all_accounts()
    accounts_list[0]["expiresAt"] = store.now_ms() - account.DAY
    store.write_accounts(accounts_list)

    kind, _ = account.classify("黑A11111")
    check("过期会员被识别为过期", account.is_expired(account.find_by_plate("黑A11111")))
    check("过期会员降级为临停", kind == account.TEMPORARY, kind)

    r = parking.handle_entry("黑A11111")
    check("过期会员入场走 temp 通道", r.get("channel") == "temp", str(r.get("channel")))

    # 停 2 小时应该收 10 元
    spots = store.read_all()
    for s in spots:
        if s.get("plate") == "黑A11111":
            s["entryTime"] = store.now_ms() - 2 * 60 * 60 * 1000
    store.write_all(spots)
    e = parking.handle_exit("黑A11111")
    check("过期会员按临停计费 10 元", e.get("fee") == 10, "实际 %d" % e.get("fee", -1))


def test_member_not_charged():
    """★ 会员（含年租）出场费用恒为 0。"""
    reset_data()
    account.register("黑A11111", account.MONTHLY, "张月租")
    account.register("黑A22222", account.YEARLY, "李年租")

    for p in ["黑A11111", "黑A22222"]:
        parking.handle_entry(p)

    for p in ["黑A11111", "黑A22222"]:
        spots = store.read_all()
        for s in spots:
            if s.get("plate") == p:
                s["entryTime"] = store.now_ms() - 3 * account.DAY
        store.write_all(spots)
        e = parking.handle_exit(p)
        check("%s 停 3 天费用为 0" % p, e.get("fee") == 0,
              "实际 %d" % e.get("fee", -1))
        check("%s 通道是 member" % p, e.get("channel") == "member", str(e.get("channel")))


def test_deduct_balance():
    """余额扣减：够就扣，不够就不扣。"""
    reset_data()
    account.recharge("黑A11111", account.MONTHLY, 1)
    check("初始余额 300", account.find_by_plate("黑A11111")["balance"] == 300)

    r = account.deduct("黑A11111", 100)
    check("扣款成功", r.get("ok") is True, r.get("msg", ""))
    check("余额剩 200", r.get("balance") == 200, str(r.get("balance")))

    r2 = account.deduct("黑A11111", 500)
    check("余额不够时不扣款", r2.get("ok") is False)
    check("返回差额 300", r2.get("short") == 300, str(r2.get("short")))
    check("余额没变", account.find_by_plate("黑A11111")["balance"] == 200,
          str(account.find_by_plate("黑A11111")["balance"]))

    r3 = account.deduct("黑A99999", 10)
    check("没账户的车扣款失败", r3.get("ok") is False)


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
    except Exception as e:
        # 超时/连接错误要带上是哪个 URL，否则排查时无从下手
        raise RuntimeError("GET %s 失败: %s" % (path, e))


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
    # ★ 服务器输出必须写到文件，不能用 PIPE。
    #   PIPE 的缓冲区（64KB）满了之后，Flask 写访问日志会被阻塞，
    #   整个服务器就卡死，表现为某个请求莫名其妙 timed out。
    #   请求数一多（v0.3 加了手机适配检查后有 200+ 个请求）必然踩到这个坑。
    log_path = os.path.join(tempfile.gettempdir(), "parking_selftest_server.log")
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [PY, os.path.join(ROOT, "app.py")],
        cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT,
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

        # 15. 切换账户类型（月租 ↔ 年租）
        status, url, text = post("/accounts/toggle",
                                  {"plate": "黑A66666", "kind": "yearly"})
        check("POST /accounts/toggle 切换成功",
              status == 200 and "ok=" in url, url)

        # 16. 删除账户
        status, url, text = post("/accounts/delete", {"plate": "黑A66666"})
        check("POST /accounts/delete 删除成功",
              status == 200 and "ok=" in url, url)

        # 17. 404
        status, _ = get("/no-such-page")
        check("不存在的页面返回 404", status == 404, "status=%d" % status)

        # 18. 充值页能打开
        status, text = get("/recharge")
        check("GET /recharge 200", status == 200, "status=%d" % status)
        check("充值页含月租套餐", "月租套餐" in text)
        check("充值页含年租套餐", "年租套餐" in text)
        check("充值页含套餐价格表", "折合每月" in text)

        # 19. 充值页第一步
        status, text = get("/recharge?plate=%E9%BB%91A77777")
        check("充值页能带上车牌", "黑A77777" in text)
        check("充值页第一步显示选套餐", "第一步 · 选套餐" in text)
        # 用 period-list 容器判断第二步是否渲染（标题文字在 HTML 注释里也会出现）
        check("充值页第一步不渲染第二步", "period-list" not in text)

        # 20. 提交套餐选择 → 显示第二步选时长
        status, url, text = post("/recharge/select",
                                  {"plate": "黑A77777", "kind": "monthly"})
        check("提交套餐后显示第二步", "第二步 · 选时长" in text)
        check("第二步渲染出档位列表", "period-list" in text)
        check("第二步含档位价格 850", "850" in text)
        check("第二步含各档位", "1 个月" in text and "12 个月" in text)

        # 21. 提交档位 → 显示确认页
        status, url, text = post("/recharge/select",
                                  {"plate": "黑A77777", "kind": "monthly",
                                   "periods": "3"})
        check("提交档位后显示确认页", "本次充值" in text)
        check("确认页显示 850 元", "850" in text)
        check("确认页有确认充值按钮", "确认充值" in text)

        # 22. 确认充值
        status, url, text = post("/recharge/do",
                                  {"plate": "黑A77777", "kind": "monthly",
                                   "periods": "3"})
        check("POST /recharge/do 充值成功",
              status == 200 and "ok=" in url, url)

        # 23. 账户管理页能看到余额
        status, text = get("/accounts")
        check("账户列表显示黑A77777", "黑A77777" in text)
        check("账户列表显示余额 850", "850" in text)
        check("账户页含充值记录", "充值记录" in text)

        # 24. 非法车牌充值被拦
        status, url, text = post("/recharge/do",
                                  {"plate": "abc", "kind": "monthly",
                                   "periods": "1"})
        check("非法车牌充值被拦",
              status == 200 and "msg=" in url, url)

        # 25. 车主端有跳充值页的入口
        status, text = get("/")
        check("车主端有充值入口", 'href="/recharge"' in text)

        # ====================================================
        # 26. 手机端适配（v0.3）
        # ====================================================
        # 这里查的是「源码里该有的东西有没有」，真正的像素级效果
        # 用 CDP 设备模拟截图人工确认过（390px / 320px 两个视口）。

        MOBILE_PAGES = ["/", "/scan", "/accounts", "/admin", "/recharge"]
        for path in MOBILE_PAGES:
            status, text = get(path)
            check("%s 有 viewport meta" % path,
                  'name="viewport"' in text and "width=device-width" in text)
            check("%s 声明了 viewport-fit=cover" % path,
                  "viewport-fit=cover" in text)
        # 27. 表格卡片化：关键表格的 td 必须带 data-label，
        #     否则手机端转成卡片后就没有字段名了
        status, text = get("/accounts")
        check("账户列表 td 带 data-label", text.count('data-label=') >= 7,
              "实际 %d 处" % text.count('data-label='))
        check("账户列表表头含车牌列", ">车牌<" in text)

        status, text = get("/admin")
        check("流水表 td 带 data-label", text.count('data-label=') >= 7,
              "实际 %d 处" % text.count('data-label='))

        # 28. CSS 里必须有手机断点和卡片化规则
        status, css = get("/static/style.css")
        check("CSS 有手机断点", "@media (max-width: 640px)" in css)
        check("CSS 有表格卡片化规则",
              "attr(data-label)" in css and "table thead" in css)
        check("CSS 有安全区适配", "safe-area-inset-bottom" in css)
        check("CSS 统计卡用了 grid", "grid-template-columns: repeat(auto-fit" in css)

        # 29. iOS 缩放：输入框字号必须 ≥ 16px，否则 Safari 聚焦时会放大页面
        sizes = re.findall(r"\.input\s*\{[^}]*font-size:\s*(\d+)px", css)
        check("输入框字号 ≥ 16px（防 iOS 自动缩放）",
              all(int(s) >= 16 for s in sizes) if sizes else False,
              "实际字号 %s" % sizes)

        # 30. 触控目标：主按钮和输入框高度 ≥ 44px
        btn_h = re.search(r"\.btn\s*\{[^}]*height:\s*(\d+)px", css)
        check("主按钮高度 ≥ 44px",
              btn_h is not None and int(btn_h.group(1)) >= 44,
              "实际 %s" % (btn_h.group(1) if btn_h else "未找到"))
        input_h = re.search(r"\.input\s*\{[^}]*height:\s*(\d+)px", css)
        check("输入框高度 ≥ 44px",
              input_h is not None and int(input_h.group(1)) >= 44,
              "实际 %s" % (input_h.group(1) if input_h else "未找到"))

        # 31. 竖排表单必须把 flex 改成 none
        #     （column 方向下 flex-basis 会盖掉 height，导致输入框塌陷）
        check("竖排表单修正了 flex-basis 塌陷问题",
              "flex: none" in css)

        # 32. 局域网访问提示（手机怎么连）
        status, text = get("/")
        check("车主端含局域网访问提示", "手机访问" in text or "lan-tip" in text)

    except Exception as e:
        check("请求过程无异常", False, str(e))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        log_file.close()


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
    test_temp_account_lifecycle()
    test_temp_account_not_created_for_member()
    test_temp_account_only_one()
    test_temp_account_upgrade_to_member()
    test_packages()
    test_recharge()
    test_recharge_yearly()
    test_expired_member_downgrades()
    test_member_not_charged()
    test_deduct_balance()

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
