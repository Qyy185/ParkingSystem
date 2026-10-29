"""
业务逻辑层
===========

核心函数：

| 函数 | 职责 |
|---|---|
| `find_free_spot(spots)` | 车位分配：找第一个空闲车位 |
| `calc_fee(entryTime)` | 分级计费：30 分钟免费 → 5 元/小时 → **每天**封顶 40 |
| `handle_entry(plate)` | 入场：校验车牌 + 查重 + **按账户分流** + 分配车位 + 记流水 |
| `handle_exit(plate)` | 出场计费：**只算钱，不放行** |
| `handle_pay(plate)` | 缴费放行：释放车位 + 删临时账号 + 记流水 |
| `find_in_field(plate)` | 查某车是否在场，返回在场车位 |

设计要点：
- 出场与缴费分成两步，为将来接支付接口留路径。
- 车牌进业务层先归一化，保证「黑a 12345」和「黑A12345」是同一辆车。
- **分流靠车牌，不靠车位**：入场时拿车牌查账户，账户标记这辆车是
  月租/年租还是临停。月租/年租 → `channel = "member"`，不计费；其余 → `channel = "temp"`，正常计费。
- **临时账号在场**：临停车入场时自动建一个临时账号，出场缴费后自动删掉。
  会员账户不受影响。
"""

import math

import account
import plate
import store


# ---------------------------------------------------------
# 【已给】初始车位配置：64 个车位，不分区
# ---------------------------------------------------------
# v0.4.2：从「A 区 8 个 + B 区 8 个」改成 64 个车位、**取消了分区**。
#
# 为什么取消分区：
#   原来 A/B 两区只是初始数据的划分，计费逻辑里根本没用到 zone ——
#   分流靠的是车牌查账户，不是车位。所以这个字段属于「有数据没用途」，
#   去掉之后数据结构更干净，页面上也不用显示一堆没信息量的标签。
#
# 车位编号：P-01 ~ P-64（P = Parking）。
#   保留字母前缀是为了以后真要分区/分层还能加，
#   现在它只是个编号前缀，不代表有分区。
#
# ★ 数据迁移（v0.4.2）：
#   老版本存过的 parking.json 是 16 个带 zone 的车位。
#   get_spots() 会检测到结构不对（数量不对 / 还带 zone），
#   自动重建为 64 个新车位 —— 已占用的车会按原顺序保留。
SPOT_COUNT = 64
SPOT_PREFIX = "P"


def init_spots():
    """生成 64 个初始车位。"""
    spots = []
    for i in range(1, SPOT_COUNT + 1):
        spots.append({
            "id": "%s-%02d" % (SPOT_PREFIX, i),
            "status": "free",
            "plate": None,
            "entryTime": None,
            # v0.2.1：记录这辆车走的哪条通道（monthly / temp）
            "channel": None,
        })
    return spots


def _needs_migration(spots):
    """判断存下来的车位数据是不是老结构。"""
    if not isinstance(spots, list) or not spots:
        return True
    if len(spots) != SPOT_COUNT:
        return True
    # 老结构每个车位都带 zone 字段
    if any("zone" in s for s in spots if isinstance(s, dict)):
        return True
    return False


def migrate_spots(old_spots):
    """老结构 → 新结构。已占用的车位尽量保留。

    老数据是 16 个带 zone 的车位，新的是 64 个。
    直接丢掉会丢失「场里有哪些车」，所以把已占用的车按原顺序
    填回新车位的前面几个位置。
    """
    new_spots = init_spots()
    if not isinstance(old_spots, list):
        return new_spots

    # 把老数据里还占着位的车按顺序收集出来
    occupied = []
    for s in old_spots:
        if not isinstance(s, dict):
            continue
        if s.get("status") == "occupied" and s.get("plate"):
            occupied.append({
                "status": "occupied",
                "plate": s.get("plate"),
                "entryTime": s.get("entryTime"),
                "channel": s.get("channel"),
            })

    # 填进新车位（从 1 开始往后排）
    for i, o in enumerate(occupied[:SPOT_COUNT]):
        new_spots[i].update(o)
    return new_spots


def get_spots():
    """从 data/parking.json 读所有车位。

    三种情况：
      · 文件是空 / 结构不对 → 用初始配置生成
      · 文件是老结构（16 个带 zone）→ 迁移成 64 个新结构
      · 正常 → 直接用
    """
    spots = store.read_all()
    if not spots or not isinstance(spots, list):
        spots = init_spots()
        store.write_all(spots)
        return spots

    if _needs_migration(spots):
        spots = migrate_spots(spots)
        store.write_all(spots)

    return spots


def find_in_field(plate_no):
    """查这辆车在不在场内。

    返回在场车位对象；不在场返回 None。
    车牌先归一化再比对，所以大小写、空格、连字符不影响判断。
    """
    _, spot = find_in_field_with_spots(plate_no)
    return spot


def find_in_field_with_spots(plate_no):
    """查在场车辆，同时把车位列表一起返回。

    ⚠️ 必须用这个而不是自己再调一次 get_spots()：
    `get_spots()` 每次都从文件重新读，返回的是**全新的对象**。
    如果查重时读一次、改状态时又读一次，改的不是同一批对象，
    `write_all()` 会把没改过的旧数据写回去 —— 车位永远释放不掉。
    """
    target = plate.normalize(plate_no)
    spots = get_spots()
    if not target:
        return spots, None
    for s in spots:
        if s.get("status") == "occupied" and plate.normalize(s.get("plate")) == target:
            return spots, s
    return spots, None



# ============================================================
# ✅ 找车位
# 从车位列表里找出「第一个」空闲车位，找到返回该车位对象，找不到返回 None。
# ============================================================
def find_free_spot(spots):
    for spot in spots:
        if spot["status"] == "free":
            return spot
    return None


# ============================================================
# ✅ 算钱
# 规则：
#   入场 30 分钟内免费
#   之后 5 元/小时，不满 1 小时按 1 小时算
#   **每天**封顶 40 元（跨天按天分段计算，每段各自封顶后累加）
#
# 例：停 2 天 = 第一天封顶 40 + 第二天封顶 40 = 80 元，
#     不是「总共 40 元」。这和「每天封顶」是两种规则，别混。
# ============================================================
# 计费参数（集中放这里，改规则只改这一处）
FREE_MINUTES = 30          # 免费时长（分钟）
HOUR_PRICE = 5             # 每小时单价（元）
DAILY_CAP = 40             # 每天封顶（元）
MINUTES_PER_DAY = 24 * 60  # 一天多少分钟


def calc_fee(entryTime):
    """按停放时长算应收费用。

    返回总费用（元）。
    """
    minutes = (store.now_ms() - entryTime) // 60000
    if minutes <= FREE_MINUTES:
        return 0

    total = 0
    remaining = minutes

    # 按天分段：每段独立算「免费时长 → 整小时 → 当天封顶」，然后累加
    while remaining > FREE_MINUTES:
        day_minutes = min(remaining, MINUTES_PER_DAY)
        billable = day_minutes - FREE_MINUTES
        hours = math.ceil(billable / 60)
        day_fee = hours * HOUR_PRICE
        total += min(day_fee, DAILY_CAP)
        remaining -= day_minutes

    return total


# ============================================================
# ✅ 入场
# 输入：plate 车牌字符串
# 做的事：校验车牌 → 查重（车是否已在场）→ **查账户分流**
#       → 分配车位 → 记入场时间 → 写回数据 → 记一条流水
#       → **临停车顺便建个临时账号**
# 返回：{"ok": True, "spot": 车位对象, "entryTime": 时间戳,
#        "channel": "member"/"temp", "account": 账户或 None,
#        "temp_created": 是否新建了临时账号}
#   或 {"ok": False, "msg": "车牌格式不正确…" / "这辆车已在场内，车位 A-01" / "车位已满"}
#
# ★ 分流逻辑（v0.2.1 起）：
#   拿车牌去查账户 —— 账户上的类型标记决定这辆车走哪条通道。
#   · 有月租/年租账户 → channel = "member"，入场不计费
#   · 没有 / 已过期 / 临时账号 → channel = "temp"，正常计费
#
# ★ 临时账号（v0.2.2）：
#   临停车入场时自动建一个临时账号，出场缴费后自动删掉。
#   它是「在场凭证」——让后台能看到场里有哪些临停车。
# ============================================================
def handle_entry(plate_no):
    # ① 车牌格式校验
    if not plate.is_valid(plate_no):
        return {"ok": False, "msg": plate.invalid_reason(plate_no)}

    # ② 归一化后查重：同一辆车不能占两个车位
    normalized = plate.normalize(plate_no)
    spots, existing = find_in_field_with_spots(normalized)
    if existing is not None:
        return {"ok": False,
                "msg": f"这辆车已在场内，车位 {existing['id']}"}

    # ③ 按车牌查账户分流（不是按车位）
    kind, acc = account.classify(normalized)
    is_member = kind in (account.MONTHLY, account.YEARLY)
    channel = "member" if is_member else "temp"

    # ④ 分配车位（spots 和上面查重用的是同一批对象）
    spot = find_free_spot(spots)
    if spot is None:
        return {"ok": False, "msg": "车位已满"}

    # ⑤ 改状态 + 记时间 + 存盘 + 记流水
    spot["status"] = "occupied"
    spot["plate"] = normalized
    spot["entryTime"] = store.now_ms()
    spot["channel"] = channel
    store.write_all(spots)
    store.add_log({"type": "in", "plate": normalized, "spot": spot["id"],
                   "channel": channel})

    # ⑥ 临停车入场 → 自动建临时账号（会员不动）
    temp_created = False
    if not is_member:
        r = account.create_temp_account(normalized)
        temp_created = bool(r.get("created"))
        acc = r.get("account") or acc

    return {"ok": True, "spot": spot, "entryTime": spot["entryTime"],
            "channel": channel, "account": acc, "kind": kind,
            "temp_created": temp_created}


# ============================================================
# ✅ 出场算钱（先不放行）
# 输入：plate 车牌字符串
# 做的事：找到这辆车 → 算钱 → 返回金额，但车位状态不变
# 返回：{"ok": True, "fee": 金额, "minutes": 停了多久, "channel": 通道}
#   或 {"ok": False, "msg": "没找到这辆车"}
#
# ★ 关键：这里【不改】车位状态。先算钱，等车主确认缴费才放行。
#
# ★ 会员通道（v0.2.1 起）：这辆车入场时记的是 member 通道，费用恒为 0。
#   月租/年租是「已经付过月租费」，不是「不交钱」，所以这里不该再收一次。
# ============================================================
def handle_exit(plate_no):
    normalized = plate.normalize(plate_no)
    _, spot = find_in_field_with_spots(normalized)
    if spot is None:
        return {"ok": False, "msg": "没找到这辆车"}
    minutes = (store.now_ms() - spot["entryTime"]) // 60000
    channel = spot.get("channel") or "temp"
    fee = 0 if channel == "member" else calc_fee(spot["entryTime"])
    return {"ok": True, "fee": fee, "minutes": minutes, "channel": channel}


# ============================================================
# ✅ 缴费放行
# 输入：plate 车牌字符串
# 做的事：把这辆车的车位释放（status 变 free，清空 plate 和 entryTime）
#       → 写回数据 → 记一条流水（带金额）
#       → **临停车顺便删掉它的临时账号**
# 返回：{"ok": True, "fee": 金额, "channel": 通道}
#   或 {"ok": False, "msg": "原因"}
#
# ★ 顺序陷阱：先算钱（用 entryTime），再清空。
#   calc_fee 要用 entryTime 算钱，而后面会把 entryTime 设成 None。
#
# ★ 会员通道：费用为 0，但仍记一笔 pay 流水（金额 0），
#   这样「出场」这件事在流水里留痕，后台统计能看出车确实开走了。
#
# ★ 临时账号（v0.2.2）：临停车缴费出场后删掉临时账号。
#   会员账户保留 —— 会员下次来还是会员。
#
# ★ 过期会员账户（v0.3.1）：**过了宽限期的会员账户要删**。
#   场景：车主 1 号交了一个月租，2 号租期就过了；车 3 号才开走。
#
#   ★ 宽限期（v0.3.1）：过期后**不立刻删**，先留半年。
#     · 宽限期内 → 账户保留，余额保留，续费可恢复会员身份
#     · 宽限期满仍未续费 → 车辆下次出场时注销
#
#   怎么区分「要删的」和「要留的」？
#   · 有效会员走 member 通道，压根不会进这段删除逻辑
#   · 过期会员入场时被 classify() 判成临停，走 temp 通道
#   · 所以在 temp 通道里再查一次账户：
#       - kind 是 monthly/yearly 且**过了宽限期** → 删
#       - kind 是 monthly/yearly 但**还在宽限期** → 保留
#       - kind 是 temporary → 删（临时账号的老规矩）
#
#   ⚠️ 删之前要把余额记进流水。账户一删余额就没了，
#     不留痕的话这笔钱就凭空消失了。
# ============================================================
def handle_pay(plate_no, method="", discount=0):
    """缴费放行（v0.4.4 起支持记录支付方式和优惠抵扣）。

    参数：
      method    支付方式标签（"微信支付" / "无感支付" 等），写进流水供报表统计
      discount  优惠券抵扣金额（元），写进流水供报表统计让利
    """
    normalized = plate.normalize(plate_no)
    spots, spot = find_in_field_with_spots(normalized)
    if spot is None:
        return {"ok": False, "msg": "没找到这辆车"}
    minutes = (store.now_ms() - spot["entryTime"]) // 60000
    channel = spot.get("channel") or "temp"
    fee = 0 if channel == "member" else calc_fee(spot["entryTime"])
    spot["status"] = "free"
    spot["plate"] = None
    spot["entryTime"] = None
    spot["channel"] = None
    store.write_all(spots)
    store.add_log({"type": "pay", "plate": normalized, "spot": spot["id"],
                   "minutes": minutes, "fee": fee, "channel": channel,
                   "method": method, "discount": int(discount or 0)})

    # 临停车出场 → 删掉它的临时账号（会员账户不动）
    temp_removed = None
    expired_removed = None
    kept_in_grace = False
    if channel == "temp":
        acc = account.find_by_plate(normalized)
        if acc is not None and account.is_member_kind(acc.get("kind")):
            if account.is_obsolete(acc):
                # ★ 过了宽限期 → 出库即注销（v0.3.1）
                balance = int(acc.get("balance") or 0)
                r = account.remove(normalized)
                expired_removed = r.get("removed")
                # 余额留痕 —— 账户删了钱不能凭空消失
                if expired_removed is not None:
                    store.add_log({
                        "type": "remove",
                        "plate": normalized,
                        "spot": spot["id"],
                        "fee": 0,
                        "channel": channel,
                        "balance": balance,
                        "reason": "租期过期且已过宽限期，出场时账户注销",
                    })
            else:
                # 还在宽限期 → 账户保留，续费可恢复会员
                kept_in_grace = True
        else:
            r = account.remove_temp_account(normalized)
            temp_removed = r.get("removed")

    return {"ok": True, "fee": fee, "channel": channel,
            "temp_removed": temp_removed,
            "expired_removed": expired_removed,
            "kept_in_grace": kept_in_grace}



