"""
账户模块
=========

**账户是什么**：一个车牌对应一个账户，账户上只挂一个「类型标记」。

| 类型 | 含义 | 后续怎么处理 |
|---|---|---|
| `monthly` | 月租车主 | 走月租通道，入场不产生计费 |
| `temporary` | 临停车主 | 走临停通道，按小时/日计费 |

**账户不干什么**（这是这个模块最重要的设计）：
- 不记金额、不记余额、不记付款
- 不判断合同是否到期（那是 v0.2.2 的事）
- 不做鉴权登录（账户不需要密码）

它只回答一个问题：**这辆车按哪套规则处理。**
"""

import plate
import store

# 账户类型常量
MONTHLY = "monthly"
TEMPORARY = "temporary"

KIND_LABELS = {
    MONTHLY: "月租",
    TEMPORARY: "临停",
}

KIND_DESCRIPTIONS = {
    MONTHLY: "月租车主，入场不计停车费",
    TEMPORARY: "临时停车，按小时与日计费",
}


def kind_label(kind):
    """类型 → 中文标签。未知类型一律当临停处理。"""
    return KIND_LABELS.get(kind, KIND_LABELS[TEMPORARY])


def all_accounts():
    """读所有账户。"""
    return store.read_accounts()


def save_accounts(accounts):
    """写所有账户。"""
    store.write_accounts(accounts)


def find_by_plate(plate_no):
    """按车牌查账户。

    车牌先归一化再比对，所以「黑a 12345」能查到「黑A12345」的账户。

    找不到返回 None。
    """
    target = plate.normalize(plate_no)
    if not target:
        return None
    for acc in store.read_accounts():
        if plate.normalize(acc.get("plate")) == target:
            return acc
    return None


def classify(plate_no):
    """判断这辆车属于哪一类。

    返回 (kind, account)：
      · kind = "monthly"    → 该车有月租账户
      · kind = "temporary"  → 有临停账户，或者压根没登记过（默认按临停）

    **没登记过的车也返回 "temporary"** —— 现实里陌生人开来就是临停，
    不能因为没账户就拒绝入场。
    """
    acc = find_by_plate(plate_no)
    if acc is None:
        return TEMPORARY, None
    kind = acc.get("kind")
    if kind not in (MONTHLY, TEMPORARY):
        kind = TEMPORARY
    return kind, acc


def register(plate_no, kind=TEMPORARY, holder="", note=""):
    """登记一个新账户。

    车牌已存在就更新它的类型和备注（相当于「改绑」）。
    返回 {"ok": True, "account": 账户} 或 {"ok": False, "msg": 原因}
    """
    if not plate.is_valid(plate_no):
        return {"ok": False, "msg": plate.invalid_reason(plate_no)}
    if kind not in (MONTHLY, TEMPORARY):
        return {"ok": False, "msg": "账户类型只能是月租或临停"}

    normalized = plate.normalize(plate_no)
    accounts = store.read_accounts()

    existing = None
    for acc in accounts:
        if plate.normalize(acc.get("plate")) == normalized:
            existing = acc
            break

    if existing is not None:
        existing["kind"] = kind
        if holder:
            existing["holder"] = holder.strip()
        if note:
            existing["note"] = note.strip()
        store.write_accounts(accounts)
        return {"ok": True, "account": existing, "created": False}

    account = {
        "plate": normalized,
        "kind": kind,
        "holder": holder.strip(),
        "note": note.strip(),
        "createdAt": store.now_ms(),
    }
    accounts.append(account)
    store.write_accounts(accounts)
    return {"ok": True, "account": account, "created": True}


def change_kind(plate_no, kind):
    """改账户类型（月租 ↔ 临停）。

    返回 {"ok": True, "account": 账户} 或 {"ok": False, "msg": 原因}
    """
    if kind not in (MONTHLY, TEMPORARY):
        return {"ok": False, "msg": "账户类型只能是月租或临停"}
    normalized = plate.normalize(plate_no)
    accounts = store.read_accounts()
    for acc in accounts:
        if plate.normalize(acc.get("plate")) == normalized:
            acc["kind"] = kind
            store.write_accounts(accounts)
            return {"ok": True, "account": acc}
    return {"ok": False, "msg": "这个车牌没有登记账户"}


def remove(plate_no):
    """删除账户。

    返回 {"ok": True, "removed": 账户} 或 {"ok": False, "msg": 原因}
    """
    normalized = plate.normalize(plate_no)
    accounts = store.read_accounts()
    kept = []
    removed = None
    for acc in accounts:
        if removed is None and plate.normalize(acc.get("plate")) == normalized:
            removed = acc
            continue
        kept.append(acc)
    if removed is None:
        return {"ok": False, "msg": "这个车牌没有登记账户"}
    store.write_accounts(kept)
    return {"ok": True, "removed": removed}


def stats():
    """账户统计，给后台页面顶部用。"""
    accounts = store.read_accounts()
    monthly = sum(1 for a in accounts if a.get("kind") == MONTHLY)
    temporary = sum(1 for a in accounts if a.get("kind") == TEMPORARY)
    return {
        "total": len(accounts),
        "monthly": monthly,
        "temporary": temporary,
    }
