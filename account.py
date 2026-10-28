"""
账户模块
=========

**账户是什么**：一个车牌对应一个账户。账户分两类，来源完全不同。

| 类型 | 来源 | 生命周期 | 待遇 |
|---|---|---|---|
| `monthly` 月租 | **人工登记** | 长期存在 | 会员通道，入场不计停车费 |
| `yearly` 年租 | **人工登记** | 长期存在 | 会员通道，入场不计停车费 |
| `temporary` 临时 | **入场自动创建** | **出场自动删除** | 临停通道，按小时与日计费 |

**核心机制：临停账号是「在场凭证」**

```
入场（临停车）──→ 自动建一个临时账号  ← 入库给
   ↓
   在场期间：账号存在，后台能看到这辆车的登记信息
   ↓
出场缴费完成 ──→ 自动删掉这个临时账号 ← 出库删
```

为什么这么设计？因为临时停车本来就没有「车主身份」，
系统却需要知道「现在场里有哪些车、这些车是什么情况」。
给每辆临停车发一个临时账号，比另开一张表更省事，
而且天然和长期账户共用一套查询逻辑。

**会员账户还管什么**（v0.2.2 新增）：
- `balance` —— 账户余额。充值增加，临停消费可抵扣
- `expiresAt` —— 会员到期时间。到期后自动失效，降级成临停
- 充值记录在 `data/recharges.json`，每一笔都留痕

**账户不干什么**：
- 不做鉴权登录（账户不需要密码）
- 不记停车费流水（那是 `logs.json` 的事）

> **设计原则**：账户负责「这辆车是谁、有什么资格」，
> 付款负责「这次停放多少钱」。两条线在 `handle_exit` 里交汇，
> 但账户本身永远不记停车费流水。
"""

import plate
import store

# ---- 账户类型常量 ----
MONTHLY = "monthly"
YEARLY = "yearly"
TEMPORARY = "temporary"

KIND_LABELS = {
    MONTHLY: "月租",
    YEARLY: "年租",
    TEMPORARY: "临时",
}

KIND_DESCRIPTIONS = {
    MONTHLY: "月租车主，入场不计停车费",
    YEARLY: "年租车主，入场不计停车费",
    TEMPORARY: "临停车辆，入场自动建账号、出场自动删",
}

# ---- 会员套餐（v0.2.2）----
# periods 是可选的购买档位，每档给一个优惠价。
# 让月租买多更便宜，符合现实里「买季度比买单月划算」。

PACKAGES = {
    MONTHLY: {
        "label": "月租套餐",
        "unit": "月",
        "options": [
            {"periods": 1, "label": "1 个月", "price": 300},
            {"periods": 3, "label": "3 个月", "price": 850},
            {"periods": 6, "label": "6 个月", "price": 1600},
            {"periods": 12, "label": "12 个月", "price": 3000},
        ],
    },
    YEARLY: {
        "label": "年租套餐",
        "unit": "年",
        "options": [
            {"periods": 1, "label": "1 年", "price": 2700},
        ],
    },
}

# 一个月按 30 天算，一年按 365 天算
DAYS_PER_PERIOD = {
    MONTHLY: 30,
    YEARLY: 365,
}

MINUTE = 60 * 1000
HOUR = 60 * MINUTE
DAY = 24 * HOUR


def kind_label(kind):
    """类型 → 中文标签。未知类型一律当临时处理。"""
    return KIND_LABELS.get(kind, KIND_LABELS[TEMPORARY])


def is_member_kind(kind):
    """这个类型是不是会员（能享受不计停车费）。"""
    return kind in (MONTHLY, YEARLY)


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


def is_expired(acc):
    """账户是否已过期。

    没到期时间（expiresAt 为 0 或缺失）视为**永久有效** ——
    临时账号没有到期时间，它的存在与否只取决于车在场不在场。
    """
    if not acc:
        return False
    expires_at = acc.get("expiresAt") or 0
    if not expires_at:
        return False
    return store.now_ms() > expires_at


def classify(plate_no):
    """判断这辆车属于哪一类。

    返回 (kind, account)：
      · kind = "monthly" / "yearly" → 该车有**有效的会员账户**
      · kind = "temporary"          → 没有会员账户（含临时账号、过期会员）

    **未登记的车返回 "temporary"** —— 陌生人开来就是临停，
    不能因为没账户就拒绝入场。
    """
    acc = find_by_plate(plate_no)
    if acc is None:
        return TEMPORARY, None

    kind = acc.get("kind")
    if not is_member_kind(kind):
        # 临时账号，或数据里写坏了的类型
        return TEMPORARY, acc

    # 会员过期了 → 当成临停处理
    if is_expired(acc):
        return TEMPORARY, acc

    return kind, acc


def register(plate_no, kind=MONTHLY, holder="", note="", balance=0, expires_at=0):
    """登记一个**长期会员账户**（月租 / 年租）。

    只能登记会员。临停账号是入场自动建的，不走这里。

    车牌已存在就更新它的类型和备注（相当于「改绑」）。
    返回 {"ok": True, "account": 账户, "created": True/False} 或 {"ok": False, "msg": 原因}
    """
    if not plate.is_valid(plate_no):
        return {"ok": False, "msg": plate.invalid_reason(plate_no)}
    if not is_member_kind(kind):
        return {"ok": False, "msg": "长期账户只能是月租或年租"}

    normalized = plate.normalize(plate_no)
    accounts = store.read_accounts()

    existing = None
    for acc in accounts:
        if plate.normalize(acc.get("plate")) == normalized:
            existing = acc
            break

    if existing is not None:
        # 别把自动建的临时账号直接改成会员 —— 会员必须走充值流程
        if existing.get("kind") == TEMPORARY:
            return {"ok": False, "msg": "这是入场自动生成的临时账号，请走充值流程开通会员"}
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
        "balance": int(balance or 0),      # 账户余额（元）
        "expiresAt": int(expires_at or 0),  # 到期时间戳，0 = 永久有效
        "createdAt": store.now_ms(),
    }
    accounts.append(account)
    store.write_accounts(accounts)
    return {"ok": True, "account": account, "created": True}


def change_kind(plate_no, kind):
    """改会员账户类型：月租 ↔ 年租。

    返回 {"ok": True, "account": 账户} 或 {"ok": False, "msg": 原因}
    """
    if not is_member_kind(kind):
        return {"ok": False, "msg": "长期账户只能是月租或年租"}
    normalized = plate.normalize(plate_no)
    accounts = store.read_accounts()
    for acc in accounts:
        if plate.normalize(acc.get("plate")) == normalized:
            if acc.get("kind") == TEMPORARY:
                return {"ok": False, "msg": "临时账号不能改类型，出场后就没了"}
            acc["kind"] = kind
            store.write_accounts(accounts)
            return {"ok": True, "account": acc}
    return {"ok": False, "msg": "这个车牌没有登记账户"}


def remove(plate_no):
    """删除账户（任意类型）。

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


# ============================================================
# 临时账号：入场自动建，出场自动删
# ============================================================

def create_temp_account(plate_no, note=""):
    """入场时给临停车建一个临时账号。

    已经存在就返回现有的（不重复建）。
    如果这个车牌已经有会员账户，**不动它** —— 会员不能被临时账号覆盖。

    返回 {"ok": True, "account": 账户, "created": True/False}
    """
    normalized = plate.normalize(plate_no)
    if not normalized:
        return {"ok": False, "msg": "车牌号不能为空"}

    accounts = store.read_accounts()
    for acc in accounts:
        if plate.normalize(acc.get("plate")) == normalized:
            # 已经有账号（会员或临时），不重复建
            return {"ok": True, "account": acc, "created": False}

    account = {
        "plate": normalized,
        "kind": TEMPORARY,
        "holder": "",
        "note": note or "临停车辆，入场自动登记",
        "balance": 0,
        "expiresAt": 0,          # 临时账号无到期时间
        "createdAt": store.now_ms(),
    }
    accounts.append(account)
    store.write_accounts(accounts)
    return {"ok": True, "account": account, "created": True}


def remove_temp_account(plate_no):
    """出场时删掉临停车的临时账号。

    **只删临停账号**，会员账户不动 —— 会员下次来还是会员。

    返回 {"ok": True, "removed": 账户} 或 {"ok": True, "removed": None}（本来就没临时账号）
    """
    normalized = plate.normalize(plate_no)
    accounts = store.read_accounts()
    kept = []
    removed = None
    for acc in accounts:
        if removed is None and acc.get("kind") == TEMPORARY \
                and plate.normalize(acc.get("plate")) == normalized:
            removed = acc
            continue
        kept.append(acc)
    if removed is not None:
        store.write_accounts(kept)
    return {"ok": True, "removed": removed}


# ============================================================
# 充值（v0.2.2）
# ============================================================

def get_option(kind, periods):
    """取套餐里某个档位的选项。找不到返回 None。"""
    pkg = PACKAGES.get(kind)
    if not pkg:
        return None
    for opt in pkg["options"]:
        if opt["periods"] == periods:
            return opt
    return None


def recharge(plate_no, kind, periods):
    """给账户充值（购买套餐）。

    做的事：
      1. 校验车牌与套餐
      2. 账户不存在就自动建一个**会员账户**（充值即开户）
      3. 余额加上套餐价格
      4. 有效期从 `max(现在, 原到期时间)` 往后延

    返回 {"ok": True, "account": 账户, "amount": 金额,
          "days": 增加的天数, "expiresAt": 新的到期时间}
    或 {"ok": False, "msg": 原因}
    """
    if not plate.is_valid(plate_no):
        return {"ok": False, "msg": plate.invalid_reason(plate_no)}
    if not is_member_kind(kind):
        return {"ok": False, "msg": "只能充值月租或年租套餐"}

    option = get_option(kind, periods)
    if option is None:
        return {"ok": False, "msg": "没有这个套餐档位"}

    normalized = plate.normalize(plate_no)
    accounts = store.read_accounts()

    # 找到账户，找不到就新建
    target = None
    for acc in accounts:
        if plate.normalize(acc.get("plate")) == normalized:
            target = acc
            break

    created = False
    if target is None:
        target = {
            "plate": normalized,
            "kind": kind,
            "holder": "",
            "note": "充值开通",
            "balance": 0,
            "expiresAt": 0,
            "createdAt": store.now_ms(),
        }
        accounts.append(target)
        created = True
    elif target.get("kind") == TEMPORARY:
        # 车还在场就充了会员：把它升级成会员账号，不新建一个
        target["kind"] = kind
        target["note"] = "临停转会员"

    # 有效期的起点：没过期就从原到期时间往后延，过期了从现在开始
    now = store.now_ms()
    old_expires = target.get("expiresAt") or 0
    base = old_expires if old_expires > now else now
    days = option["periods"] * DAYS_PER_PERIOD.get(kind, 30)
    new_expires = base + days * DAY

    target["kind"] = kind
    target["balance"] = int(target.get("balance") or 0) + option["price"]
    target["expiresAt"] = new_expires
    store.write_accounts(accounts)

    store.add_recharge({
        "plate": normalized,
        "kind": kind,
        "option": option["label"],
        "amount": option["price"],
        "days": days,
    })

    return {"ok": True, "account": target, "amount": option["price"],
            "days": days, "expiresAt": new_expires, "created": created}


def deduct(plate_no, amount):
    """从账户余额里扣钱。

    余额不够就**不扣**，返回 {"ok": False, "short": 差额}。
    调用方要决定：让用户去充值，还是只扣一部分。

    返回 {"ok": True, "account": 账户, "balance": 剩余余额}
    或 {"ok": False, "msg": 原因} / {"ok": False, "short": 差额}
    """
    if amount <= 0:
        acc = find_by_plate(plate_no)
        return {"ok": True, "account": acc,
                "balance": int(acc.get("balance", 0)) if acc else 0}

    acc = find_by_plate(plate_no)
    if acc is None:
        return {"ok": False, "msg": "这个车牌没有账户"}

    balance = int(acc.get("balance") or 0)
    if balance < amount:
        return {"ok": False, "short": amount - balance, "balance": balance}

    accounts = store.read_accounts()
    for a in accounts:
        if plate.normalize(a.get("plate")) == plate.normalize(plate_no):
            a["balance"] = balance - amount
            store.write_accounts(accounts)
            return {"ok": True, "account": a, "balance": a["balance"]}
    return {"ok": False, "msg": "这个车牌没有账户"}


def stats():
    """账户统计，给后台页面顶部用。"""
    accounts = store.read_accounts()
    monthly = sum(1 for a in accounts if a.get("kind") == MONTHLY)
    yearly = sum(1 for a in accounts if a.get("kind") == YEARLY)
    temporary = sum(1 for a in accounts if a.get("kind") == TEMPORARY)
    expired = sum(1 for a in accounts
                  if is_member_kind(a.get("kind")) and is_expired(a))
    total_balance = sum(int(a.get("balance") or 0) for a in accounts)
    return {
        "total": len(accounts),
        "monthly": monthly,
        "yearly": yearly,
        "temporary": temporary,
        "expired": expired,
        "balance": total_balance,
    }
