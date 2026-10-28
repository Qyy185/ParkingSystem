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


def days_left(acc):
    """距离到期还有几天。

    返回值：
      · 正数 —— 还剩几天（0 = 今天到期）
      · 负数 —— 已经过期几天了
      · None —— 没有到期时间（永久有效），不参与催缴

    ★ 用「整天数」算，不按小时。这样 23 小时后到期算 0 天（今天到期），
    不会因为差几分钟就跳到下一档，催缴频次才稳定。
    """
    if not acc:
        return None
    expires_at = acc.get("expiresAt") or 0
    if not expires_at:
        return None
    now = store.now_ms()
    if now >= expires_at:
        # 已过期：算过期了几天
        return -((now - expires_at) // DAY)
    # 未过期：向上取整到整天
    return (expires_at - now + DAY - 1) // DAY


def expire_date_str(acc):
    """到期日期字符串（YYYY-MM-DD），没有到期时间返回空串。"""
    expires_at = (acc or {}).get("expiresAt") or 0
    if not expires_at:
        return ""
    import time as _time
    return _time.strftime("%Y-%m-%d", _time.localtime(expires_at / 1000))


# ============================================================
#  宽限期（v0.3.1）
# ============================================================
#
# 租期过期 ≠ 立刻销户。现实里车主可能出差、忘了续、或者只是晚几天，
# 立刻把账户删掉等于把客户推走。
#
# 所以过期后进入**宽限期**：
#
# ```
# 到期前  ──────► 到期 ──────► 宽限期 180 天 ──────► 注销
#   正常      过期，按临停     还能续费恢复会员        账户销毁
#                            仍然保留余额
# ```
#
# 宽限期内：
#   · 入场按**临停**计费（不能白嫖会员待遇）
#   · 续费可**恢复会员身份**，余额保留
#   · 催缴继续提醒「不续费将被注销」
#
# 宽限期满仍未续费：
#   · 账户标记为待清理
#   · **车辆下次出场时注销**（清理时机跟着车走，不定时清理）
#
# 为什么注销要绑在出场？因为账户的价值在于「这辆车下次来能认出它」。
# 车不在场的时候留着账户没意义，但也犯不着立刻删 —— 等它真来了再说，
# 如果它在这半年里续费了，就当没过期过。

GRACE_DAYS = 180          # 宽限期：半年
GRACE_MS = GRACE_DAYS * DAY


def grace_until(acc):
    """宽限期截止时间戳。

    = 原到期时间 + 180 天。没有到期时间返回 0（不适用）。
    """
    expires_at = (acc or {}).get("expiresAt") or 0
    if not expires_at:
        return 0
    return expires_at + GRACE_MS


def in_grace(acc):
    """账户是否处于「已过期但还在宽限期内」。

    这段时间账户还留着，续费能恢复会员身份。
    """
    if not acc:
        return False
    if not is_expired(acc):
        return False
    return store.now_ms() <= grace_until(acc)


def is_obsolete(acc):
    """账户是否「过宽限期了，应该清理掉」。

    = 已过期 且 已过宽限期。清理动作发生在车辆出场时。
    """
    if not acc:
        return False
    if not is_expired(acc):
        return False
    return store.now_ms() > grace_until(acc)


def grace_days_left(acc):
    """宽限期还剩几天。已过宽限期返回负数。"""
    end = grace_until(acc)
    if not end:
        return None
    now = store.now_ms()
    if now > end:
        return -((now - end) // DAY)
    return (end - now + DAY - 1) // DAY


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


def register(plate_no, kind=MONTHLY, holder="", note="", balance=0, expires_at=0,
             phone_no="", wechat_id=""):
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
        if phone_no:
            existing["phone"] = phone_no.strip()
        if wechat_id:
            existing["wechat"] = wechat_id.strip()
        store.write_accounts(accounts)
        return {"ok": True, "account": existing, "created": False}

    account = {
        "plate": normalized,
        "kind": kind,
        "holder": holder.strip(),
        "note": note.strip(),
        "balance": int(balance or 0),      # 账户余额（元）
        "expiresAt": int(expires_at or 0),  # 到期时间戳，0 = 永久有效
        "phone": phone_no.strip(),         # 手机号（催缴用，v0.3.1）
        "wechat": wechat_id.strip(),       # 微信号（催缴用，v0.3.1）
        "reminded": {},                    # 已发过的催缴档位（v0.3.1）
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
            "phone": "",
            "wechat": "",
            "reminded": {},
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

    # ★ 宽限期内续费 → 恢复会员身份。
    #   宽限期内账户没被注销，余额也还在，所以续费后余额应该累加而不是重置。
    #   这里 balance 用的是 `+=`，自然就保留了原来的余额。
    #
    #   过期超宽限期的账户（该清理了）如果还没出场，续费也能救回来 ——
    #   账户还在文件里，续费就等于重新开户，不需要特殊处理。
    was_in_grace = in_grace(target)
    if was_in_grace:
        target["note"] = "宽限期内续费，已恢复会员"

    target["kind"] = kind
    target["balance"] = int(target.get("balance") or 0) + option["price"]
    target["expiresAt"] = new_expires
    # ★ 续费后是新周期，催缴记录要清空。
    #   否则「提前 7 天」这个档位会被认为已经发过，新周期永远不再提醒。
    target["reminded"] = {}
    store.write_accounts(accounts)

    store.add_recharge({
        "plate": normalized,
        "kind": kind,
        "option": option["label"],
        "amount": option["price"],
        "days": days,
        "restored": was_in_grace,
    })

    return {"ok": True, "account": target, "amount": option["price"],
            "days": days, "expiresAt": new_expires, "created": created,
            "restored": was_in_grace}


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
    # 宽限期内：过期了但账户还保留着
    in_grace_count = sum(1 for a in accounts
                          if is_member_kind(a.get("kind")) and in_grace(a))
    # 过了宽限期：账户待清理，下次出场时注销
    obsolete = sum(1 for a in accounts
                   if is_member_kind(a.get("kind")) and is_obsolete(a))
    total_balance = sum(int(a.get("balance") or 0) for a in accounts)
    return {
        "total": len(accounts),
        "monthly": monthly,
        "yearly": yearly,
        "temporary": temporary,
        "expired": expired,
        "inGrace": in_grace_count,
        "obsolete": obsolete,
        "balance": total_balance,
    }


# ============================================================
#  催缴（v0.3.1）
# ============================================================
#
# 会员租期快到期时要主动提醒车主续费。催缴的难点不在「发消息」，
# 而在**什么时候发、发几次** —— 发太早是骚扰，发太晚车主来不及，
# 重复发更糟。
#
# **档位设计**：到期前 7 天、3 天、1 天、当天，各提醒一次。
# 过期后不再催（已经过期了，催也没用，直接按临停计费）。
#
# **去重**：每个账户记一个 `reminded` 字段，存已发过的档位：
#
# ```json
# "reminded": {"7": 1792993553950, "3": 1792993553950}
# ```
#
# 键是「档位天数」，值是发送时间戳。同一档位只发一次。
# 续费后 `expiresAt` 变了，档位重新开始算。

REMIND_STAGES = [
    {"days": 7, "label": "提前 7 天", "urgency": "normal", "phase": "before"},
    {"days": 3, "label": "提前 3 天", "urgency": "normal", "phase": "before"},
    {"days": 1, "label": "提前 1 天", "urgency": "urgent", "phase": "before"},
    {"days": 0, "label": "今天到期", "urgency": "urgent", "phase": "before"},
]

# 续费价格提示，用在催缴文案里
PRICE_HINTS = {
    MONTHLY: "（月租 300 元起）",
    YEARLY: "（年租 2700 元）",
}


def match_remind_stage(left_days):
    """按剩余天数匹配催缴档位。

    返回档位 dict，没匹配上返回 None。

    ★ 匹配规则：取「days ≥ 剩余天数」里**最小**的那个档位。

    每个档位 days 是一个**触发上界**，对应一个区间：

    | 档位        | 区间       | 含义                       |
    |-------------|-----------|----------------------------|
    | 提前 7 天   | (3, 7]    | 还剩 3~7 天，提醒续费     |
    | 提前 3 天   | (1, 3]    | 还剩 1~3 天，提醒续费     |
    | 提前 1 天   | (0, 1]    | 还剩 1 天以内，提醒续费   |
    | 今天到期    | (-∞, 0]   | 今天到期，最后一次提醒    |
    | 无          | > 7       | 还早着，不催               |

    举例：
      · 还剩 5 天 → 落在 (3,7] → 匹配「提前 7 天」
      · 还剩 2 天 → 落在 (1,3] → 匹配「提前 3 天」
      · 还剩 0 天 → 落在 (-∞,0] → 匹配「今天到期」
      · 还剩 10 天 → 不在任何区间 → None，不催

    ⚠️ 别写成「≤ 剩余天数取最大」——那样剩 10 天也会匹配到「提前 7 天」，
    等于「一周前就开始催」，是骚扰。
    """
    if left_days is None or left_days < 0:
        return None
    matched = None
    for stage in REMIND_STAGES:
        if stage["days"] >= left_days:
            if matched is None or stage["days"] < matched["days"]:
                matched = stage
    return matched


def match_grace_stage(grace_left):
    """宽限期的催缴档位。

    ★ v0.3.1：宽限期内**不催缴**。

    宽限期是纯粹的「缓冲期」—— 给车主留半年时间，账户还在、余额还在，
    随时回来续费就恢复会员。但既然已经判定他不续了，再发消息就是骚扰，
    客户体验也差（「我都半年没来了你还天天催」）。

    所以宽限期内：账户保留、不催缴、进场按临停计费、续费可恢复会员。
    保留这个函数是为了留个扩展点 —— 将来要做「宽限期末尾最后一次挽留」
    之类的策略，改这里就行。

    永远返回 None = 不催。
    """
    return None


def has_reminded(acc, stage_days):
    """这个账户在这个档位上是否已经发过催缴。"""
    reminded = (acc or {}).get("reminded") or {}
    return str(stage_days) in reminded


def mark_reminded(acc, stage_days, channel=None):
    """记下「这个档位已经催过了」。

    同时把账户的 `reminded` 整个换掉 —— 因为续费后到期时间变了，
    旧的催缴记录就不再适用，要清掉重来。
    """
    reminded = dict((acc or {}).get("reminded") or {})
    reminded[str(stage_days)] = store.now_ms()
    acc["reminded"] = reminded
    return acc


def clear_reminded(acc):
    """清空催缴记录。续费成功后调用 —— 新周期重新开始提醒。"""
    acc["reminded"] = {}
    return acc


def pending_reminders(within_days=7):
    """找出需要催缴的账户。

    返回列表，每项：
    {
      "account": 账户,
      "daysLeft": 剩余天数,
      "stage": 匹配到的档位,
      "expireDate": 到期日期,
      "reminded": 该档位是否已发过,
      "targets": 可用的发送目标 [{"channel": "sms", "target": "138xxxx"}]
    }

    ★ 只催**未过期**的会员账户（within_days 天内到期）。

    已过期的分两种，都不催：
      · 还在宽限期内 → 账户保留、余额保留，不打扰（v0.3.1 决定）
      · 过了宽限期   → 账户待清理，已经不是要挽回的客户了
    """
    result = []
    for acc in store.read_accounts():
        if not is_member_kind(acc.get("kind")):
            continue
        left = days_left(acc)
        if left is None:
            continue          # 永久有效，不催
        if left < 0:
            continue          # 已过期（含宽限期内），一律不催
        if left > within_days:
            continue
        stage = match_remind_stage(left)
        if stage is None:
            continue
        result.append({
            "account": acc,
            "daysLeft": left,
            "stage": stage,
            "expireDate": expire_date_str(acc),
            "reminded": has_reminded(acc, stage["days"]),
            "targets": available_targets(acc),
        })
    # 越接近到期排越前
    result.sort(key=lambda x: x["daysLeft"])
    return result


def available_targets(acc):
    """这个账户有哪些可用的发送目标。

    一个账户可能同时有手机号和微信号，那就两个渠道都能发。
    """
    targets = []
    phone_no = (acc.get("phone") or "").strip()
    wechat_id = (acc.get("wechat") or "").strip()
    if phone_no:
        targets.append({"channel": "sms", "target": phone_no})
    if wechat_id:
        targets.append({"channel": "wechat", "target": wechat_id})
    return targets


def set_contact(plate_no, phone_no="", wechat_id=""):
    """给账户补联系方式（手机号 / 微信号）。

    传空字符串表示不修改该字段。
    返回 {"ok": True, "account": 账户} 或 {"ok": False, "msg": 原因}
    """
    normalized = plate.normalize(plate_no)
    if not normalized:
        return {"ok": False, "msg": "车牌号不能为空"}
    accounts = store.read_accounts()
    for acc in accounts:
        if plate.normalize(acc.get("plate")) == normalized:
            if phone_no:
                acc["phone"] = phone_no.strip()
            if wechat_id:
                acc["wechat"] = wechat_id.strip()
            store.write_accounts(accounts)
            return {"ok": True, "account": acc}
    return {"ok": False, "msg": "这个车牌没有账户"}
