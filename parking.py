"""
业务逻辑层
===========

下面 5 个函数全部已实现并通过自检（12/12）：

| 函数 | 职责 |
|---|---|
| `find_free_spot(spots)` | 车位分配：找第一个空闲车位 |
| `calc_fee(entryTime)` | 分级计费：30 分钟免费 → 5 元/小时 → 每天封顶 40 |
| `handle_entry(plate)` | 入场：分配车位 + 记时间戳 + 存盘 + 记流水 |
| `handle_exit(plate)` | 出场计费：**只算钱，不放行** |
| `handle_pay(plate)` | 缴费放行：释放车位 + 存盘 + 记流水 |

设计要点：出场与缴费分成两步，为将来接支付接口留路径。
"""

import math

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
#   每天封顶 40 元
# ============================================================
def calc_fee(entryTime):
    minutes = (store.now_ms() - entryTime) // 60000
    if minutes <= 30:
        return 0
    billable_minutes = minutes - 30
    hours = math.ceil(billable_minutes / 60)
    fee = hours * 5
    if fee > 40:
        fee = 40
    return fee


# ============================================================
# ✅ 入场
# 输入：plate 车牌字符串
# 做的事：找车位 → 记入场时间 → 写回数据 → 记一条流水
# 返回：{"ok": True, "spot": 车位对象, "entryTime": 时间戳}
#   或 {"ok": False, "msg": "车位已满"}
# ============================================================
def handle_entry(plate):
    spots = get_spots()
    spot = find_free_spot(spots)
    if spot is None:
        return {"ok": False, "msg": "车位已满"}
    spot["status"] = "occupied"
    spot["plate"] = plate
    spot["entryTime"] = store.now_ms()
    store.write_all(spots)
    store.add_log({"type": "in", "plate": plate, "spot": spot["id"]})
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
def handle_exit(plate):
    spots = get_spots()
    matches = [s for s in spots if s["plate"] == plate]
    if not matches:
        return {"ok": False, "msg": "没找到这辆车"}
    spot = matches[0]
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
def handle_pay(plate):
    spots = get_spots()
    matches = [s for s in spots if s["plate"] == plate]
    if not matches:
        return {"ok": False, "msg": "没找到这辆车"}
    spot = matches[0]
    fee = calc_fee(spot["entryTime"])
    minutes = (store.now_ms() - spot["entryTime"]) // 60000
    spot["status"] = "free"
    spot["plate"] = None
    spot["entryTime"] = None
    store.write_all(spots)
    store.add_log({"type": "pay", "plate": plate, "spot": spot["id"],
                  "minutes": minutes, "fee": fee})
    return {"ok": True}



