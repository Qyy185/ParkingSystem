"""
催缴业务模块
============

**把「谁要催」和「怎么发」串起来。**

```
account.pending_reminders()  →  找出该催的人（account.py 负责）
        ↓
remind.send_reminders()      →  生成文案 + 发出去（notify.py 负责发送）
        ↓
store.add_notification()     →  留痕（store.py 负责）
```

分工：
- `account.py` —— 谁能被催、什么时候该催、这个档位催过没有
- `notify.py`  —— 消息怎么发出去（渠道抽象）
- `remind.py`  —— 把上面两个串起来，生成文案、批量发送

## 去重是第一原则

催缴最怕**重复骚扰**。同一个账户在同一个档位上只能发一次。
去重靠账户上的 `reminded` 字段，由 `account.has_reminded()` 判断。

续费后 `recharge()` 会清空 `reminded`，新周期重新开始提醒。
"""

import account
import notify
import store


def send_reminder(plate_no, channel_id=None, force=False):
    """给单个账户发催缴。

    参数：
      · plate_no   —— 车牌
      · channel_id —— 指定渠道；不传就发到所有可用渠道
      · force      —— True 时跳过去重检查，强制再发一次（催缴测试用）

    返回 {"ok": True, "sent": [...], "skipped": 原因}
    或 {"ok": False, "msg": 原因}
    """
    acc = account.find_by_plate(plate_no)
    if acc is None:
        return {"ok": False, "msg": "这个车牌没有账户"}
    if not account.is_member_kind(acc.get("kind")):
        return {"ok": False, "msg": "只有月租 / 年租账户需要催缴"}

    left = account.days_left(acc)
    if left is None:
        return {"ok": False, "msg": "这个账户没有到期时间，不需要催缴"}
    if left < 0:
        # ★ 宽限期内不催（v0.3.1）
        if account.in_grace(acc):
            g_left = account.grace_days_left(acc)
            return {"ok": False,
                    "msg": "已过期但在宽限期内（还剩 %d 天），不催缴" % g_left}
        return {"ok": False, "msg": "这个账户已经过期且过了宽限期，按临停计费即可"}

    stage = account.match_remind_stage(left)
    if stage is None:
        return {"ok": False, "msg": "还没到催缴时间（%d 天后）" % left}

    # 去重检查
    if not force and account.has_reminded(acc, stage["days"]):
        return {"ok": False,
                "msg": "%s 档位已经催过了，不重复发送" % stage["label"]}

    targets = account.available_targets(acc)
    if not targets:
        return {"ok": False, "msg": "这个账户没有手机号或微信号，发不出去"}

    if channel_id:
        targets = [t for t in targets if t["channel"] == channel_id]
        if not targets:
            return {"ok": False, "msg": "这个账户没有该渠道的联系方式"}

    content = notify.build_reminder_text(
        acc, left, stage["label"], account.expire_date_str(acc),
        account.PRICE_HINTS.get(acc.get("kind"), ""))

    sent = []
    for t in targets:
        r = notify.send(t["channel"], t["target"], content)
        if r.get("ok"):
            sent.append(r)
        else:
            sent.append(r)

    # 记下这个档位已催过
    accounts = store.read_accounts()
    for a in accounts:
        if a.get("plate") == acc.get("plate"):
            account.mark_reminded(a, stage["days"])
            break
    store.write_accounts(accounts)

    return {
        "ok": True,
        "sent": sent,
        "stage": stage,
        "daysLeft": left,
        "content": content,
        "targets": targets,
    }


def send_all_reminders(within_days=7, channel_id=None):
    """批量催缴：给所有该催的账户都发一遍。

    返回 {"ok": True, "sent": n, "skipped": n, "results": [...]}
    """
    pending = account.pending_reminders(within_days)
    results = []
    sent_count = 0
    skipped_count = 0

    for item in pending:
        acc = item["account"]
        r = send_reminder(acc["plate"], channel_id)
        if r.get("ok"):
            sent_count += 1
        else:
            skipped_count += 1
        results.append({
            "plate": acc["plate"],
            "holder": acc.get("holder", ""),
            "daysLeft": item["daysLeft"],
            "stage": item["stage"]["label"],
            "ok": bool(r.get("ok")),
            "msg": r.get("msg", "") if not r.get("ok") else "已发送",
            "sentCount": len([s for s in r.get("sent", []) if s.get("ok")]),
        })

    return {
        "ok": True,
        "sent": sent_count,
        "skipped": skipped_count,
        "total": len(pending),
        "results": results,
    }
