"""
业务逻辑层 —— 你的主战场
=========================

下面 5 个函数里，**前 2 个你已经写完了**（find_free_spot / calc_fee），
**后 3 个留给你**（handle_entry / handle_exit / handle_pay）。

写之前先想清楚：输入是什么、输出是什么、边界情况是什么。
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
# ✅ 你已写完：找车位
# 从车位列表里找出「第一个」空闲车位，找到返回该车位对象，找不到返回 None。
# ============================================================
def find_free_spot(spots):
    for spot in spots:
        if spot["status"] == "free":
            return spot
    return None


# ============================================================
# ✅ 你已写完：算钱
# 规则：
#   入场 30 分钟内免费
#   之后 5 元/小时，不满 1 小时按 1 小时算
#   每天封顶 40 元
# ============================================================
def calc_fee(entryTime):
    import math
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
# ⬜ TODO 1 —— 入场
# 输入：plate 车牌字符串
# 做的事：找车位 → 记入场时间 → 写回数据 → 记一条流水
# 返回：{"ok": True, "spot": 车位对象, "entryTime": 时间戳}
#   或 {"ok": False, "msg": "车位已满"}
#
# 步骤：
#   1. spots = get_spots()
#   2. spot = find_free_spot(spots)
#   3. spot 为 None → return {"ok": False, "msg": "车位已满"}
#   4. spot["status"] = "occupied"
#      spot["plate"] = plate
#      spot["entryTime"] = store.now_ms()
#   5. store.write_all(spots)          ← 少了这行，刷新页面就没了
#   6. store.add_log({"type": "in", "plate": plate, "spot": spot["id"]})
#   7. return {"ok": True, "spot": spot, "entryTime": spot["entryTime"]}
#
# ⚠️ Python 提示：
#   · 判断空用 `is None`，不要用 `== None`
#   · 取字典值用 spot["id"]，不是 spot.id
#   · ⚠️ 状态字段 status 和 plate 必须一起改，不能只改一个
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
# ⬜ TODO 2 —— 出场算钱（先不放行）
# 输入：plate 车牌字符串
# 做的事：找到这辆车 → 算钱 → 返回金额，但车位状态不变
# 返回：{"ok": True, "fee": 金额, "minutes": 停了多久}
#   或 {"ok": False, "msg": "没找到这辆车"}
#
# 步骤：
#   1. spots = get_spots()
#   2. 找到车牌等于 plate 的那个车位
#      提示：[s for s in spots if s["plate"] == plate]
#   3. 没找到 → return {"ok": False, "msg": "没找到这辆车"}
#   4. fee = calc_fee(spot["entryTime"])
#   5. minutes = (store.now_ms() - spot["entryTime"]) // 60000
#   6. ★ 这里【不要】改车位状态！先算钱，等车主确认缴费才放行
#   7. return {"ok": True, "fee": fee, "minutes": minutes}
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
# ⬜ TODO 3 —— 缴费放行
# 输入：plate 车牌字符串
# 做的事：把这辆车的车位释放（status 变 free，清空 plate 和 entryTime）
#       → 写回数据 → 记一条流水（带金额）
# 返回：{"ok": True} 或 {"ok": False, "msg": "原因"}
#
# 步骤：
#   1. spots = get_spots()
#   2. 找到车牌等于 plate 的那个车位
#   3. 没找到 → return {"ok": False, "msg": "没找到这辆车"}
#   4. 记下要写流水的金额：fee = calc_fee(spot["entryTime"])
#      记下时长：minutes = (store.now_ms() - spot["entryTime"]) // 60000
#   5. spot["status"] = "free"
#      spot["plate"] = None
#      spot["entryTime"] = None
#   6. store.write_all(spots)
#   7. store.add_log({"type": "pay", "plate": plate, "spot": spot["id"],
#                     "minutes": minutes, "fee": fee})
#   8. return {"ok": True}
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



