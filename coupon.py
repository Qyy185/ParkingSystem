"""
优惠券模块（v0.4.4）
=====================

**优惠券是什么**：管理员发放的一串折扣码，临停缴费时输入，直接抵扣现金。

```
管理员发券（/coupons）──→ data/coupons.json
        ↓
车主缴费时输入优惠码（模拟收银台）
        ↓
后端校验：存在？没停用？没过期？没用完？这辆车没用过？
        ↓
校验通过 → 抵扣 → 记录「谁在哪次用的、抵了多少」
```

**为什么校验放在后端重做一遍**：

和支付确认页同一个道理 —— 前端传上来的任何东西都不可信。
GET 渲染时校验过一次没用，POST 提交时必须重新查一遍：
过期时间到了、次数被别人用完了、同一辆车重复提交，都可能发生在两次请求之间。

**一条优惠码的生命周期**：

```
创建（active=True）──→ 停用（active=False，还能看不能改）
        ↓                        ↓
     被使用 usedCount+1      到期 expiresAt 过了
        ↓
  usedCount == maxUses → 自动「已领完」
```

**一条使用记录长什么样**：

```json
"usedBy": [
  {"plate": "黑A12345", "time": 1795300000000, "fee": 10, "discount": 3}
]
```

记 `fee` 和 `discount` 是为了对账：报表页能看到「优惠让利了多少」，
纠纷时也能查「这笔到底收了多少」。

**优惠券不干什么**：
- 不做领券中心、不做满减梯度（演示环境一条码一个固定面额就够）
- 不限车型、不限时段 —— 这些是真实上线后的营销配置，属于运营后台的事
"""

import plate
import store

MINUTE = 60 * 1000
HOUR = 60 * MINUTE
DAY = 24 * HOUR


def _normalize_code(code):
    """优惠码归一化：去掉所有空白（含内部空格）+ 转大写。

    「abc123」「ABC123」「abc 123」必须是同一张券，不然对不上账。
    和车牌归一化一个思路：用户敲进去的东西不干净，先洗干净再比对。
    """
    return "".join((code or "").split()).upper()


def all_coupons():
    """读所有优惠券。"""
    return store.read_coupons()


def save_coupons(coupons):
    """写所有优惠券。"""
    store.write_coupons(coupons)


def find(code):
    """按码查优惠券（大小写不敏感）。找不到返回 None。"""
    target = _normalize_code(code)
    if not target:
        return None
    for c in store.read_coupons():
        if (c.get("code") or "").upper() == target:
            return c
    return None


def create(code, discount, max_uses=1, valid_days=30):
    """发一张新优惠券。

    参数：
      code        优惠码（唯一，大小写不敏感）
      discount    面额（元），整数，抵扣时不会超过停车费本身
      max_uses    最多能被多少辆车使用
      valid_days  发放后多少天内有效

    返回 {"ok": True, "coupon": 券} 或 {"ok": False, "msg": 原因}
    """
    normalized = _normalize_code(code)
    if not normalized:
        return {"ok": False, "msg": "优惠码不能为空"}
    if len(normalized) > 20:
        return {"ok": False, "msg": "优惠码太长了（最多 20 位）"}

    try:
        discount = int(discount)
    except (TypeError, ValueError):
        return {"ok": False, "msg": "面额必须是数字"}
    if discount <= 0:
        return {"ok": False, "msg": "面额必须大于 0"}

    try:
        max_uses = int(max_uses)
    except (TypeError, ValueError):
        return {"ok": False, "msg": "可用次数必须是数字"}
    if max_uses <= 0:
        return {"ok": False, "msg": "可用次数必须大于 0"}

    try:
        valid_days = int(valid_days)
    except (TypeError, ValueError):
        return {"ok": False, "msg": "有效天数必须是数字"}
    if valid_days <= 0:
        return {"ok": False, "msg": "有效天数必须大于 0"}

    if find(normalized) is not None:
        return {"ok": False, "msg": "优惠码 %s 已存在" % normalized}

    cp = {
        "code": normalized,
        "discount": discount,
        "maxUses": max_uses,
        "usedCount": 0,
        "usedBy": [],                       # [{plate, time, fee, discount}]
        "expiresAt": store.now_ms() + valid_days * DAY,
        "active": True,
        "createdAt": store.now_ms(),
    }
    coupons = store.read_coupons()
    coupons.append(cp)
    store.write_coupons(coupons)
    return {"ok": True, "coupon": cp}


def set_active(code, active):
    """停用 / 恢复一张券。停用后校验直接不通过，但记录保留（可审计）。"""
    target = _normalize_code(code)
    coupons = store.read_coupons()
    for c in coupons:
        if (c.get("code") or "").upper() == target:
            c["active"] = bool(active)
            store.write_coupons(coupons)
            return {"ok": True, "coupon": c}
    return {"ok": False, "msg": "没有这张优惠码"}


def remove(code):
    """删除一张券。只有没用过的才能删 —— 用过的要留对账。"""
    target = _normalize_code(code)
    coupons = store.read_coupons()
    kept = []
    removed = None
    for c in coupons:
        if removed is None and (c.get("code") or "").upper() == target:
            removed = c
            continue
        kept.append(c)
    if removed is None:
        return {"ok": False, "msg": "没有这张优惠码"}
    if removed.get("usedCount"):
        return {"ok": False, "msg": "这张券已被使用 %d 次，不能删（留着对账）"
                % removed["usedCount"]}
    store.write_coupons(kept)
    return {"ok": True, "removed": removed}


def _is_expired(cp):
    expires_at = cp.get("expiresAt") or 0
    return bool(expires_at) and store.now_ms() > expires_at


def status(cp):
    """券的当前状态标签：生效 / 已停用 / 已领完 / 已过期。"""
    if not cp.get("active"):
        return "已停用"
    if _is_expired(cp):
        return "已过期"
    if int(cp.get("usedCount") or 0) >= int(cp.get("maxUses") or 0):
        return "已领完"
    return "生效中"


def _plate_used(cp, plate_no):
    """这辆车是否已经用过这张券。"""
    target = plate.normalize(plate_no)
    for u in cp.get("usedBy") or []:
        if plate.normalize(u.get("plate")) == target:
            return True
    return False


def validate(code, plate_no, amount):
    """校验一张券能不能用、能抵多少。**只校验，不记账**。

    返回 {"ok": True, "code": ..., "discount": 实际抵扣额}
    或 {"ok": False, "msg": 不能用的原因}

    ★ 抵扣额 = min(券面额, 停车费)。10 元的券抵 5 元的停车费，
      实收 0 元，不能倒找钱。
    """
    cp = find(code)
    if cp is None:
        return {"ok": False, "msg": "优惠码不存在"}
    if not cp.get("active"):
        return {"ok": False, "msg": "优惠码已停用"}
    if _is_expired(cp):
        return {"ok": False, "msg": "优惠码已过期"}
    if int(cp.get("usedCount") or 0) >= int(cp.get("maxUses") or 0):
        return {"ok": False, "msg": "优惠码已被领完"}
    if _plate_used(cp, plate_no):
        return {"ok": False, "msg": "这个车牌已经用过这张优惠码"}

    try:
        amount = int(amount or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        return {"ok": False, "msg": "本次费用为 0，无需使用优惠"}

    discount = min(int(cp.get("discount") or 0), amount)
    return {"ok": True, "code": cp["code"], "discount": discount}


def consume(code, plate_no, fee, discount):
    """核销一张券。**必须先 validate 通过再调这个**。

    把「谁、什么时候、那笔费用多少、抵了多少」记进 usedBy。

    返回 {"ok": True, "coupon": 券} 或 {"ok": False, "msg": 原因}
    """
    cp = find(code)
    if cp is None:
        return {"ok": False, "msg": "优惠码不存在"}

    # 核销前再校验一遍 —— 两次请求之间券可能刚好被用完/过期
    v = validate(code, plate_no, fee)
    if not v.get("ok"):
        return v

    target = _normalize_code(code)
    coupons = store.read_coupons()
    for c in coupons:
        if (c.get("code") or "").upper() == target:
            c["usedCount"] = int(c.get("usedCount") or 0) + 1
            c.setdefault("usedBy", []).append({
                "plate": plate.normalize(plate_no),
                "time": store.now_ms(),
                "fee": int(fee or 0),
                "discount": int(discount or 0),
            })
            store.write_coupons(coupons)
            return {"ok": True, "coupon": c}
    return {"ok": False, "msg": "优惠码不存在"}


def stats():
    """优惠券统计，给管理页顶部用。"""
    coupons = store.read_coupons()
    total_given = sum(int(c.get("discount") or 0) * int(c.get("usedCount") or 0)
                      for c in coupons)
    return {
        "total": len(coupons),
        "active": sum(1 for c in coupons if status(c) == "生效中"),
        "usedCount": sum(int(c.get("usedCount") or 0) for c in coupons),
        "given": total_given,
    }
