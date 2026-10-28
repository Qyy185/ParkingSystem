"""
业务逻辑层
===========

下面 6 个函数全部已实现并通过自检：

| 函数 | 职责 |
|---|---|
| `find_free_spot(spots)` | 车位分配：找第一个空闲车位 |
| `calc_fee(entryTime)` | 分级计费：30 分钟免费 → 5 元/小时 → **每天**封顶 40 |
| `handle_entry(plate)` | 入场：校验车牌 + 查重 + 分配车位 + 记时间戳 + 存盘 + 记流水 |
| `handle_exit(plate)` | 出场计费：**只算钱，不放行** |
| `handle_pay(plate)` | 缴费放行：释放车位 + 存盘 + 记流水 |
| `find_in_field(plate)` | 查某车是否在场，返回在场车位 |

设计要点：
- 出场与缴费分成两步，为将来接支付接口留路径。
- 车牌进业务层先归一化，保证「黑a 12345」和「黑A12345」是同一辆车。
"""

import math

import plate
import store


# ---------------------------------------------------------
# 【已给】初始车位配置：A 区 8 个 + B 区 8 个，共 16 个
# ---------------------------------------------------------
def init_spots():
    """生成 16 个初始车位。"""
    spots = []
    for letter in ["A", "B"]:
        for i in range(1, 9):
            spots.append({
                "id": f"{letter}-{i:02d}",
                "zone": f"{letter}区",
                "status": "free",
                "plate": None,
                "entryTime": None,
            })
    return spots


def get_spots():
    """从 data/parking.json 读所有车位。文件是空的就用初始配置生成一份。"""
    spots = store.read_all()
    if not spots or not isinstance(spots, list):
        spots = init_spots()
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
# 做的事：校验车牌 → 查重（车是否已在场）→ 分配车位 → 记入场时间
#       → 写回数据 → 记一条流水
# 返回：{"ok": True, "spot": 车位对象, "entryTime": 时间戳}
#   或 {"ok": False, "msg": "车牌格式不正确…" / "这辆车已在场内，车位 A-01" / "车位已满"}
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

    # ③ 分配车位（spots 和上面查重用的是同一批对象）
    spot = find_free_spot(spots)
    if spot is None:
        return {"ok": False, "msg": "车位已满"}

    # ④ 改状态 + 记时间 + 存盘 + 记流水
    spot["status"] = "occupied"
    spot["plate"] = normalized
    spot["entryTime"] = store.now_ms()
    store.write_all(spots)
    store.add_log({"type": "in", "plate": normalized, "spot": spot["id"]})
    return {"ok": True, "spot": spot, "entryTime": spot["entryTime"]}


# ============================================================
# ✅ 出场算钱（先不放行）
# 输入：plate 车牌字符串
# 做的事：找到这辆车 → 算钱 → 返回金额，但车位状态不变
# 返回：{"ok": True, "fee": 金额, "minutes": 停了多久}
#   或 {"ok": False, "msg": "没找到这辆车"}
#
# ★ 关键：这里【不改】车位状态。先算钱，等车主确认缴费才放行。
# ============================================================
def handle_exit(plate_no):
    normalized = plate.normalize(plate_no)
    _, spot = find_in_field_with_spots(normalized)
    if spot is None:
        return {"ok": False, "msg": "没找到这辆车"}
    fee = calc_fee(spot["entryTime"])
    minutes = (store.now_ms() - spot["entryTime"]) // 60000
    return {"ok": True, "fee": fee, "minutes": minutes}


# ============================================================
# ✅ 缴费放行
# 输入：plate 车牌字符串
# 做的事：把这辆车的车位释放（status 变 free，清空 plate 和 entryTime）
#       → 写回数据 → 记一条流水（带金额）
# 返回：{"ok": True} 或 {"ok": False, "msg": "原因"}
#
# ★ 顺序陷阱：先算钱（用 entryTime），再清空。
#   calc_fee 要用 entryTime 算钱，而后面会把 entryTime 设成 None。
# ============================================================
def handle_pay(plate_no):
    normalized = plate.normalize(plate_no)
    spots, spot = find_in_field_with_spots(normalized)
    if spot is None:
        return {"ok": False, "msg": "没找到这辆车"}
    fee = calc_fee(spot["entryTime"])
    minutes = (store.now_ms() - spot["entryTime"]) // 60000
    spot["status"] = "free"
    spot["plate"] = None
    spot["entryTime"] = None
    store.write_all(spots)
    store.add_log({"type": "pay", "plate": normalized, "spot": spot["id"],
                   "minutes": minutes, "fee": fee})
    return {"ok": True}



