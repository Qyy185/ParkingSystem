"""
通知模块
========

**催缴功能的消息出口。** 会员租期快到期时，系统要主动提醒车主续费。

## 为什么单独抽一个模块

催缴要发到**不同的地方**（短信、微信，将来可能是公众号/APP 推送）。
如果把发送逻辑写进 `account.py`，加一个渠道就要改业务层，违反单一职责。

所以这里定义一个**渠道抽象**：

```python
class Channel:
    name = "渠道名"
    def send(self, target, content): ...
```

业务层只管「什么时候该催、催谁、说什么」，**不关心怎么发出去**。

## 现在是模拟的，但接口是真的

现实里发短信要买短信服务商的接口、申请签名模板，有钱有资质才能发。
这个项目没有这些条件，所以：

- 渠道实现为 `MockChannel`：**不真的发**，而是把「发给谁、发什么」记到
  `data/notifications.json`，页面上能看到「模拟已发送」
- 但**调用方看到的接口和真实渠道完全一样**：
  `send(target, content) -> {"ok": True, "channel": "sms", "messageId": "..."}`

将来要接真的服务，只需实现一个真的 Channel 类，替换掉 mock，业务层一行不动。

```python
class RealSmsChannel(Channel):
    name = "sms"
    def send(self, target, content):
        # 调短信服务商的 API
        return {"ok": True, "channel": "sms", "messageId": real_id}
```
"""

import store

# ---- 渠道标识 ----
SMS = "sms"
WECHAT = "wechat"

CHANNEL_LABELS = {
    SMS: "短信",
    WECHAT: "微信",
}


def channel_label(name):
    """渠道标识 → 中文名。"""
    return CHANNEL_LABELS.get(name, name)


# ---------------------------------------------------------
#  渠道抽象
# ---------------------------------------------------------
class Channel:
    """通知渠道的基类。

    子类必须实现 `send`。返回结构固定为：
    {"ok": True/False, "channel": 渠道名, "messageId": 消息ID, "msg": 失败原因}
    """

    name = "base"

    def send(self, target, content):
        """把 `content` 发给 `target`（手机号或微信号）。"""
        raise NotImplementedError


class MockChannel(Channel):
    """模拟渠道：只记录，不真发。

    消息记到 `data/notifications.json`，页面上能看到完整的发送记录，
    包括「发给谁」「发的是什么」「什么时候发的」。这既是演示素材，
    也是审计留痕 —— 催缴这种动作必须能查。
    """

    def __init__(self, name):
        self.name = name

    def send(self, target, content):
        if not target:
            return {"ok": False, "channel": self.name, "msg": "没有联系方式"}

        record = {
            "channel": self.name,
            "target": target,
            "content": content,
            "time": store.now_ms(),
            "status": "simulated",   # 模拟发送。真实渠道这里应该是 "sent"
        }
        store.add_notification(record)
        return {
            "ok": True,
            "channel": self.name,
            "messageId": "sim-%d" % record["time"],
            "record": record,
        }


# 当前启用的渠道。真实化时在这里替换成真的 Channel 实例即可。
CHANNELS = {
    SMS: MockChannel(SMS),
    WECHAT: MockChannel(WECHAT),
}


def available_channels():
    """可用渠道列表，给页面下拉框用。"""
    return [{"id": cid, "label": channel_label(cid), "mock": True}
            for cid in CHANNELS]


def send(channel_id, target, content):
    """通过指定渠道发一条消息。

    返回 {"ok": True/False, "channel": 渠道名, "messageId": ..., "msg": 原因}
    """
    channel = CHANNELS.get(channel_id)
    if channel is None:
        return {"ok": False, "channel": channel_id, "msg": "没有这个通知渠道"}
    return channel.send(target, content)


# ============================================================
#  催缴文案
# ============================================================

def build_reminder_text(acc, days_left, days_label, expire_date, price_hint):
    """生成催缴文案。

    参数：
      · acc         —— 账户
      · days_left   —— 距离到期还有几天（0 = 今天到期）
      · days_label  —— 档位标签，如「提前 7 天」
      · expire_date —— 到期日期字符串（YYYY-MM-DD）
      · price_hint  —— 续费价格提示，如「月租 300 元/月」

    文案要短 —— 短信超过 70 字会被截断，微信也看不下去。
    """
    holder = acc.get("holder") or "车主"
    kind_label = "月租" if acc.get("kind") == "monthly" else "年租"
    plate_no = acc.get("plate", "")

    if days_left > 0:
        head = "【停车缴费】%s您好，您的%s（%s）" % (holder, kind_label, plate_no)
        body = "将于 %s 到期，剩余 %d 天，请及时续费。" % (expire_date, days_left)
    elif days_left == 0:
        head = "【停车缴费】%s您好，您的%s（%s）" % (holder, kind_label, plate_no)
        body = "今天到期！续费后继续享会员通道，不计停车费。"
    else:
        head = "【停车缴费】%s您好，您的%s（%s）" % (holder, kind_label, plate_no)
        body = "已于 %s 到期，现按临停计费，请尽快续费。" % expire_date

    return head + body + price_hint
