# 地下停车场管理系统

> 一个用 **Python + Flask** 写的地下停车场管理系统，**零 JavaScript**。
> 车主扫码入场，系统按车牌查账户分流：会员不计停车费，临停按小时计费。

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**当前版本：v0.4.5**（本地口令与公网一致 + 登录后回原页面）

> v0.4.5 修复：本地双击 启动.bat 时 `ACCESS_PWD` / `ADMIN_PWD` 只认环境变量，
> 导致没配口令时管理端被静默关闭——访问 `/admin`、`/accounts`、`/remind`
> 被一声不吭弹回车主端（浏览器标签全显示「车主端」）。
> 现在口令优先级与公网部署一致：**环境变量 > 项目根的
> `access-pwd.txt` / `admin-pwd.txt` > 空**，本地和公网用同一套口令。
> 另外口令页登录成功后会回到你本来要去的页面（带 `next` 参数），
> 不再一律甩到首页。想在本地关掉口令：设环境变量 `ACCESS_PWD=""` 即可。

| 页面 | 地址 |
|---|---|
| 车主端 | `http://localhost:3000` |
| 扫码入场 | `http://localhost:3000/scan` |
| **充值** | `http://localhost:3000/recharge` |
| 账户管理 | `http://localhost:3000/accounts` |
| **催缴管理** | `http://localhost:3000/remind` |
| **优惠券** | `http://localhost:3000/coupons`（管理端） |
| **收入报表** | `http://localhost:3000/report`（管理端） |
| **模拟清单** | `http://localhost:3000/mocks`（管理端） |
| 管理后台 | `http://localhost:3000/admin` |

> 📱 **手机访问**：启动后车主端页脚会显示本机局域网地址（如 `http://192.168.1.5:3000`），
> 手机连同一个 Wi-Fi 直接在浏览器里输这个地址就行，不用装任何东西。
>
> 🌐 **公网访问**：见 [公网部署](#公网部署) 一节，用 GitHub Actions 一条命令拿到公网 URL。

---

## 这是什么

一个**完整可跑的小型业务系统**，覆盖停车场最核心的闭环：

```
扫码入场 ──→ 查账户分流 ──→ 分配车位 ──→ 在场计费 ──→ 缴费放行 ──→ 记账 ──→ 后台统计
```

**核心函数**：

| 函数 | 职责 | 关键点 |
|---|---|---|
| `find_free_spot(spots)` | 车位分配 | 遍历找第一个 `status == "free"` 的 |
| `find_in_field(plate)` | 在场查询 | 归一化后比对，找不到返回 `None` |
| `calc_fee(entryTime)` | 分级计费 | 30 分钟免费 → 5 元/小时 → **每天**封顶 40 |
| `handle_entry(plate)` | 入场 | 校验车牌 + 查重 + **按账户分流** + **建临时账号** + 记流水 |
| `handle_exit(plate)` | 出场计费 | **只算钱，不放行**；会员通道费用恒为 0 |
| `handle_pay(plate)` | 缴费放行 | 释放车位 + **删临时账号** + 记流水 |

---

## 快速开始

```bash
# 1. 建虚拟环境
python -m venv .venv

# 2. 装依赖
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows

# 3. 启动
.venv/Scripts/python.exe app.py
```

**Windows 用户可以直接双击 `启动.bat`。**

---

## 特色一：按车牌分流

**分流靠车牌，不靠车位。** 入口扫到车牌 → 查这个车牌绑定的账户 → 账户上的类型标记
决定这辆车走哪条通道。

| 账户类型 | 通道 | 入场 | 出场 |
|---|---|---|---|
| `monthly` 月租 | 会员通道 | 不计费 | 费用 **0 元** |
| `yearly` 年租 | 会员通道 | 不计费 | 费用 **0 元** |
| 没有会员账户 | 临停通道 | 正常计费 | 按小时与日计费 |

> **未登记的车不拒绝入场。** 现实里陌生人开来就是临停，
> 不能因为没账户就把人挡在门外。

---

## 特色二：账户分两种，来源完全不同

| 类型 | 来源 | 生命周期 | 待遇 |
|---|---|---|---|
| `monthly` 月租 | **人工登记 / 充值开通** | 长期存在 | 会员通道，不计停车费 |
| `yearly` 年租 | **人工登记 / 充值开通** | 长期存在 | 会员通道，不计停车费 |
| `temporary` 临时 | **入场自动创建** | **出场自动删除** | 临停通道，按小时与日计费 |

### 临时账号是「在场凭证」

```
入场（临停车）──→ 自动建一个临时账号  ← 入库给
   ↓
   在场期间：账号存在，后台能看到这辆车的登记信息
   ↓
出场缴费完成 ──→ 自动删掉这个临时账号 ← 出库删
```

为什么这么设计？临时停车本来就没有「车主身份」，
但系统需要知道「现在场里有哪些车」。给每辆临停车发一个临时账号，
比另开一张表更省事，而且天然和长期账户共用一套查询逻辑。

**会员账户不受影响** —— 会员入场不建临时账号，出场后账户照旧保留。

### 会员账户还管什么

| 字段 | 含义 |
|---|---|
| `balance` | 账户余额。充值增加，可用于抵扣临停费用 |
| `expiresAt` | 会员到期时间。**到期后自动降级成临停**，照常计费 |

---

## 特色三：独立的充值页面

**充值页面和停车费付款页面是分开的**：

| 页面 | 用途 | 地址 |
|---|---|---|
| 停车费付款 | 这次停放多少钱 | 车主端 `/` → 查费 → 缴费 |
| 会员充值 | 买月租 / 年租套餐 | `/recharge` |

但**付款页可以跳过来** —— 车主端有「去充值开通会员」入口，
临停车付费的用户可以直接转化。

### 套餐

| 套餐 | 时长 | 价格 | 折合每月 |
|---|---|---|---|
| 月租 | 1 个月 | 300 元 | 300 元 |
| 月租 | 3 个月 | 850 元 | 约 283 元 |
| 月租 | 6 个月 | 1600 元 | 约 267 元 |
| 月租 | 12 个月 | 3000 元 | 250 元 |
| **年租** | 1 年 | **2700 元** | **225 元** |

买得越多越便宜，年租最划算。

### 充值流程

```
输车牌 → 选套餐（月租 / 年租）→ 选时长档位 → 确认充值
   ↓
余额增加 + 有效期顺延（从 max(现在, 原到期时间) 起算）+ 记一笔充值流水
```

充值即开户：车没账户也能直接充，充完就是会员。

**零 JavaScript** —— 三步全靠 HTML 表单 POST 串联。

---

## 特色四：模拟扫码入场

`/scan` 页面模拟现实里的**车牌识别摄像头**：

```
点「模拟扫描」 → 取景框进入识别态 → 输入摄像头读到的车牌 → 确认入场
```

识别到车牌后，页面直接显示这辆车走哪条通道（月租 / 临停），不用等入场完才知道。

---

## 特色五：手机端适配（v0.3）

**这不是「加个 media query 就完事」**，实测发现三个真问题并逐个解决了。

### 问题一：宽表格在手机上没法用

账户列表 7 列、流水表 7 列，在 390px 屏幕上需要横向滚动，
用户得左右拖才能看到「删除」按钮，而且完全不知道右边还有内容。

**解法：表格卡片化。** 小屏时每一行变成一张卡片，字段名显示在左边：

```
桌面（宽表）              手机（卡片）
┌────┬───┬────┐         ┌─────────────────┐
│车牌│类型│余额│         │ 车牌    黑A22222 │
├────┼───┼────┤         │ 类型    [年租]   │
│黑A │月租│850 │         │ 余额    2700 元  │
└────┴───┴────┘         │ 操作  [改月租][删除]│
                         └─────────────────┘
```

**纯 CSS 实现，零 JavaScript** —— 靠 `data-label` 属性提供字段名：

```html
<td data-label="车牌">黑A22222</td>
```

```css
@media (max-width: 640px) {
  .table thead { display: none; }          /* 表头没用了 */
  .table td::before {
    content: attr(data-label);              /* 字段名从 data-label 来 */
    color: #8a9099; font-size: 12px;
  }
}
```

### 问题二：iOS 输入框会让页面自动放大

`.input` 原来字号 15px。**iOS Safari 对小于 16px 的输入框，聚焦时会强制放大页面**，
整个布局跟着跳动，用户得点一下屏幕才能继续。

改成 16px 就没这问题。同理按钮高度从 42px 提到 **44px**（Apple HIG 的触摸标准）。

### 问题三：刘海屏 / 底部小白条遮挡内容

`viewport-fit=cover` 让背景铺满全屏，代价是内容会顶到刘海下面。
用 `env(safe-area-inset-*)` 把内容缩回来：

```css
@supports (padding: max(0px)) {
  .wrap {
    padding-left: max(16px, env(safe-area-inset-left));
    padding-bottom: calc(40px + env(safe-area-inset-bottom));
  }
}
```

### 断点设计

| 视口 | 布局 |
|---|---|
| ≤ 640px（手机） | 表格转卡片、统计卡 6 格网格、导航 2×2、车位格子 3 列 |
| 641~1023px（平板） | 表格保持表格 + 横向滚动、统计卡自适应列 |
| ≥ 1024px（桌面） | 完整表格 + 8 列车位格 |

### 验证方式

用 **Chrome DevTools Protocol 设备模拟**在 390×844（iPhone 12）和 320×568（iPhone SE）
两个视口下逐页截图 + 测量 `scrollWidth`，确认 10 个页面-视口组合**全部无横向溢出**。

```python
# 关键指标：scrollWidth 必须等于 clientWidth
res = Runtime.evaluate("document.documentElement.scrollWidth")
```

> ⚠️ **别用 `--window-size` 截图来判断响应式**。那个参数下 Edge 的实际视口
> 和截图尺寸对不上，会误判成「整页被裁」。必须用 CDP 的
> `Emulation.setDeviceMetricsOverride` 做真机模拟。

---

## 特色六：会员催缴（v0.3.1）

**租期快到期时主动提醒车主续费。**

### 催缴档位

到期前 **7 天 / 3 天 / 1 天 / 当天** 各提醒一次。每个档位是**触发上界**，
对应一个区间：

| 档位 | 区间 | 含义 |
|---|---|---|
| 提前 7 天 | (3, 7] | 还剩 3~7 天，提醒续费 |
| 提前 3 天 | (1, 3] | 还剩 1~3 天，提醒续费 |
| 提前 1 天 | (0, 1] | 还剩 1 天以内 |
| 今天到期 | (-∞, 0] | 最后一次提醒 |
| — | > 7 | 还早着，**不催** |

```python
# 取「days ≥ 剩余天数」里最小的档位
matched = None
for stage in REMIND_STAGES:
    if stage["days"] >= left_days:
        if matched is None or stage["days"] < matched["days"]:
            matched = stage
```

> ⚠️ 别写成「≤ 剩余天数取最大」——那样剩 10 天也会匹配到「提前 7 天」，
> 等于**一周前就开始催**，是骚扰。

### 去重是第一原则

催缴最怕重复骚扰。账户上记一个 `reminded` 字段，存已发过的档位：

```json
"reminded": { "7": 1792993553950, "3": 1792993553950 }
```

同一档位只发一次。**续费后清空这个字段**，新周期重新开始提醒。

### 渠道抽象（模拟发送，可换真实实现）

```python
class Channel:
    def send(self, target, content): ...

class MockChannel(Channel):
    """模拟渠道：只记录，不真发。"""
    def send(self, target, content):
        store.add_notification({...})   # 留痕
        return {"ok": True, "channel": self.name, "messageId": "..."}
```

现实里发短信要买服务商接口、申请签名模板。这个项目没有条件，所以用
`MockChannel` —— **但调用方看到的接口和真实渠道完全一样**。
将来接真服务只需实现一个真的 Channel 类替换掉 mock，业务层一行不动。

消息记到 `data/notifications.json`，页面上能看到完整的发送记录
（发给谁、发的是什么、什么时候发的），催缴这个动作是可审计的。

### 宽限期：过期不等于立刻销户

现实里车主可能出差、忘了续、只是晚几天。立刻销户等于把客户推走。

```
到期前 ──────► 到期 ──────► 宽限期 180 天 ──────► 注销
  正常      过期，按临停     还能续费恢复会员        账户销毁
                          仍然保留余额
```

| 阶段 | 入场 | 催缴 | 出场 | 账户 |
|---|---|---|---|---|
| 未到期 | 会员通道，0 元 | 正常催 | 保留 | 保留 |
| **宽限期内** | 临停通道，按时计费 | **不催** | 保留 | 保留，余额还在 |
| 过宽限期 | 临停通道，按时计费 | 不催 | **注销** | 删除，余额留痕 |

- **宽限期内续费 → 恢复会员身份**，余额累加而不是重置
- **过宽限期 + 车辆出场 → 账户注销**，余额写进流水留痕（钱不能凭空消失）
- 清理时机**跟着车走**，不定时清理——车不在场时留着账户没意义，
  但也不必立刻删，等它真来了再说

---

## 特色七：两段式出场流程

`handle_exit` **只算钱，不释放车位**；`handle_pay` 才真正放行。

这对应真实停车场的流程，也给支付接口留了扩展路径：

```
handle_exit  →  算钱、返回金额、状态不变
handle_pay   →  用户确认付款后，才把车位释放
```

将来接微信支付，就是「算钱 → 用户付 → 放行」，两个函数都不用重写。

---

## 特色八：数据持久化与流水审计

五张 JSON 表：`data/parking.json`（车位）、`data/logs.json`（停车流水）、
`data/accounts.json`（账户）、`data/recharges.json`（充值流水）、
`data/notifications.json`（催缴发送记录）。

```python
# store.py —— 异常兜底，文件坏了也不能让服务挂掉
def _read_list(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []
```

流水表的字段是**数据契约**——每个字段都有下游消费者：

| 字段 | 用途 | 谁在用 |
|---|---|---|
| `type` | 区分入场 / 缴费 | `today_revenue()` 筛出缴费记录算营收 |
| `plate` | 车牌 | 按车牌追溯记录 |
| `spot` | 车位号 | 追溯车位历史 |
| `minutes` | 停放时长 | 统计平均停放时长 |
| `fee` | 金额 | 后台显示 + 营收累加 |
| `channel` | 通道 | 区分会员 / 临停 |

**充值流水和停车费流水是两张表** —— 充值是买套餐，停车费是这次停放的钱，
两回事，不混在一起。

---

## 特色九：优惠券 / 无感支付 / 收入报表（v0.4.4）

对标市面商业停车系统（捷顺、科拓、ETC 无感支付）补齐的三块商业能力。

### 优惠券：发券 → 输码 → 抵扣 → 对账

```
管理员发券（/coupons）──→ data/coupons.json
        ↓
车主在收银台输入优惠码
        ↓
后端重新校验：存在？没停用？没过期？没用完？这辆车没用过？
        ↓
抵扣（不超过停车费本身）→ 核销留痕（谁用的、抵了多少）
```

校验规则和支付金额一样：**前端传的都不算数，提交时后端重查一遍**。
GET 渲染之后券可能刚好过期、刚被领完，所以 POST 必须重新校验。
被用过的券禁止删除 —— 使用记录要留着对账。

### 无感支付：余额够就自动扣款放行

充值时可勾选开通（账户管理页也能开关）。开通后出场的分流：

```
出场算钱
  ├─ 费用 0（免费时段/会员）→ 直接放行，不进收银台
  ├─ 开了无感支付 且 余额够 → 自动扣款放行（跳过收银台）
  └─ 其余 → 模拟收银台人工确认
```

典型场景：会员过期进入宽限期的车，入场按临停计费，
出场时余额够就直接扣，这就是真实系统里「免密扣款、出场秒抬杆」的体验。
★ 顺序不能反：先扣款再 `handle_pay` —— 放行时临时账号会被删掉，删完就没地方扣了。

### 收入报表：按天对账

`/report` 按天分列四组数字：临停折前、优惠让利、临停实收、充值收入。
实收 = 折前 − 让利 + 充值。缴费流水新增 `method`（支付方式）和 `discount`（抵扣）
两个字段，报表纯读流水，不含业务逻辑。柱状图是纯 CSS 横条，延续了零 JavaScript 的约束。

---

## 项目结构

```
.
├── app.py              # Flask 服务：路由 + 表单处理 + 模板过滤器
├── parking.py          # ★ 业务逻辑层（核心）
├── plate.py            # 车牌归一化与校验
├── account.py          # ★ 账户模块 + 套餐 + 充值 + 催缴档位 + 宽限期 + 无感支付
├── coupon.py           # ★ 优惠券：发券 + 校验 + 核销（v0.4.4）
├── mocks.py            # 模拟功能登记表（每处假的东西 + 替换方案）
├── remind.py           # ★ 催缴业务：扫描 + 发送 + 去重
├── notify.py           # ★ 通知渠道抽象（短信 / 微信，当前模拟发送）
├── store.py            # 数据读写层
├── templates/
│   ├── index.html      #   车主端
│   ├── scan.html       #   扫码入场
│   ├── recharge.html   #   充值（可顺带开通无感支付）
│   ├── pay_confirm.html#   模拟收银台（支持优惠码抵扣）
│   ├── accounts.html   #   账户管理（含无感支付开关）
│   ├── coupons.html    #   优惠券管理（v0.4.4）
│   ├── report.html     #   收入报表（v0.4.4）
│   ├── remind.html     #   催缴管理
│   ├── mocks.html      #   模拟功能清单
│   ├── _mock.html      #   模拟标注宏（角标 / 说明卡 / 横幅）
│   ├── admin.html      #   管理后台
│   └── error.html      #   404 页
├── static/
│   └── style.css       # 纯 CSS（响应式 + Grid 布局）
├── data/               # 运行时生成的数据文件（gitignore）
├── tools/
│   ├── selftest.py     # 476 项自动化自检
│   ├── test_v042.py    # 59 项端到端回归（权限 / 支付确认页）
│   ├── launcher.src.txt # 启动器源文件（UTF-8）
│   └── convert_launcher.ps1  # 转码成 GBK 的 启动.bat
├── miniprogram/        # 微信小程序（原生）
├── .github/
│   └── workflows/
│       └── deploy.yml  # 公网部署（Actions + Cloudflare 隧道）
├── DEMO.md             # 面试演示指引
├── requirements.txt
└── 启动.bat
```

**分层设计**：

| 层 | 文件 | 职责 |
|---|---|---|
| 数据层 | `store.py` | 只读写文件，不含业务逻辑 |
| 业务层 | `parking.py` + `plate.py` + `account.py` + `coupon.py` + `remind.py` | 纯逻辑，不碰 HTTP |
| 通知层 | `notify.py` | 渠道抽象，隔离「怎么发出去」 |
| 表现层 | `app.py` + `templates/` | 收请求、调逻辑、渲染页面 |

---

## 测试

```bash
.venv/Scripts/python.exe tools/selftest.py
```

自检分两部分，共 **340 项**：

**A. 业务规则单元测试**（231 项，直接调业务层，不依赖网络）

| 分组 | 覆盖内容 |
|---|---|
| 计费 | 10/30/45/90/120 分钟、8 小时、1/2/3 天封顶 |
| 车牌归一化 | 大小写、空格、连字符、全角、空输入 |
| 车牌校验 | 6 个合法车牌 + 7 个非法车牌 |
| 重复入场 | 首次成功、重复拒绝、大小写不同也算重复、只占 1 个车位 |
| 完整闭环 | 入场 → 查费 → **出场不放行** → 缴费 → 车位释放 → 流水两条 |
| 账户登记 | 登记、归一化查询、重复登记是更新、非法车牌拒、切换类型、删除 |
| 分流判断 | 月租 / 年租 / 未登记 三种情况 |
| 入场分流 | 会员走 member、临停走 temp、车位记录通道 |
| 会员不计费 | 停 3 天费用 0 元、缴费 0 元、车位正常释放 |
| 临停照常计费 | 停 2 小时 10 元（不被会员逻辑误伤） |
| **临时账号生命周期** | 入场自动建、出场自动删、会员不被覆盖、重复入场不多建 |
| **临停转会员** | 充值把临时账号升级，不新建 |
| **套餐表** | 月租 4 档年租 1 档、折合单价递减、年租最便宜 |
| **充值** | 加余额、延长期限、记流水、非法车牌/类型/档位都拒 |
| **会员过期降级** | 过期后按临停计费 10 元 |
| **余额扣减** | 够就扣、不够不扣并返回差额 |
| 账户与付款分离 | 缴停车费不改账户类型、不扣充值余额 |
| **催缴档位匹配** | 7/5/4→7 档、3/2→3 档、1→1 档、0→到期档、8+→不催 |
| **剩余天数计算** | 整天取整、23 小时算 1 天、过期算负数、永久有效返回 None |
| **待催列表筛选** | 只含未过期且在范围内的；临时账号/过期/永久有效都不进 |
| **催缴发送** | 成功、文案含车牌和日期、通知记录留痕、标记模拟发送 |
| **催缴去重** | 同档位第二次被拦、force 可强发、续费后清空记录 |
| **缺联系方式** | 没有手机号/微信号发不出去并给明确提示 |
| **宽限期判定** | 过期 2 天在宽限期、过期 200 天已过宽限期、未过期不算 |
| **宽限期内不催** | 不进待催列表、直接催被拒、消息数为 0 |
| **宽限期内续费** | 恢复会员身份、余额累加不重置、恢复后走会员通道 |
| **过期账户注销** | 过宽限期出场后账户删除、有效会员出场后保留 |
| **余额留痕** | 注销时余额写进流水，钱不凭空消失 |
| **临时账号照常删** | v0.2.2 的行为没被破坏 |
| **公网部署配置** | PORT/SECRET_KEY/ACCESS_PWD 走环境变量、secret_key 不硬编码 |
| **Actions 工作流** | gunicorn 起服务、装 cloudflared、提取 URL、写任务摘要、可手动触发、有清理步骤 |
| **访问口令保护** | 没口令跳口令页、静态资源不拦、口令错给提示、对了放行、新会话重新拦 |

**B. HTTP 端到端测试**（109 项，真起服务器，请求页面和表单）

其中最后 22 项是 **v0.3 手机端适配**的回归防护：

| 检查项 | 说明 |
|---|---|
| viewport meta | 5 个页面都有 `width=device-width` |
| viewport-fit=cover | 5 个页面都声明了，避开刘海和小白条 |
| `data-label` 覆盖 | 账户列表 / 流水表的 td 都带字段名，卡片化才不会变无名列表 |
| CSS 手机断点 | `@media (max-width: 640px)` 存在 |
| CSS 卡片化规则 | `attr(data-label)` + `table thead` 规则存在 |
| 安全区适配 | `safe-area-inset-bottom` 存在 |
| 统计卡用 grid | 不是 `flex`（flex 会被内容撑爆） |
| 输入框字号 ≥ 16px | 防 iOS Safari 聚焦时自动放大页面 |
| 按钮/输入框高度 ≥ 44px | Apple HIG 触摸标准 |
| flex-basis 塌陷修复 | 竖排表单里 `flex: none` |
| 局域网访问提示 | 页脚显示手机能访问的地址 |

> 💡 **像素级效果没法用断言测**，所以另有 CDP 设备模拟的截图人工核对
> （390×844 和 320×568 两个视口 × 5 个页面 = 10 组，全部无横向溢出）。

<details>
<summary>完整 HTTP 测试清单（点击展开）</summary>

```
[PASS] GET / 200
[PASS] 车主端含标题
[PASS] 渲染出 16 个车位
[PASS] 含入场表单
[PASS] 含出场表单
[PASS] GET /admin 200
[PASS] 后台含流水表
[PASS] GET /static/style.css 200
[PASS] POST /entry 空车牌被拦下
[PASS] POST /entry 非法车牌被拦下
[PASS] POST /entry 合法车牌入场成功
[PASS] POST /entry 重复入场被拦下
[PASS] 提示信息渲染到页面
[PASS] 后台流水时间已格式化为可读时间
[PASS] GET /scan 200
[PASS] 扫码页含取景框
[PASS] 扫码页含模拟扫描按钮
[PASS] POST /scan/detect 进入识别状态
[PASS] 扫码确认入场成功（未登记走临停）
[PASS] GET /accounts 200
[PASS] 账户页含新增表单
[PASS] POST /accounts/add 登记成功
[PASS] 账户列表里能看到新账户
[PASS] 账户列表显示月租标签
[PASS] POST /accounts/toggle 切换成功
[PASS] POST /accounts/delete 删除成功
[PASS] 不存在的页面返回 404
[PASS] GET /recharge 200
[PASS] 充值页含月租套餐
[PASS] 充值页含年租套餐
[PASS] 充值页含套餐价格表
[PASS] 充值页能带上车牌
[PASS] 充值页第一步显示选套餐
[PASS] 充值页第一步不渲染第二步
[PASS] 提交套餐后显示第二步
[PASS] 第二步渲染出档位列表
[PASS] 第二步含档位价格 850
[PASS] 第二步含各档位
[PASS] 提交档位后显示确认页
[PASS] 确认页显示 850 元
[PASS] 确认页有确认充值按钮
[PASS] POST /recharge/do 充值成功
[PASS] 账户列表显示黑A77777
[PASS] 账户列表显示余额 850
[PASS] 账户页含充值记录
[PASS] 非法车牌充值被拦
[PASS] 车主端有充值入口
[PASS] / 有 viewport meta
[PASS] / 声明了 viewport-fit=cover
[PASS] /scan 有 viewport meta
[PASS] /scan 声明了 viewport-fit=cover
[PASS] /accounts 有 viewport meta
[PASS] /accounts 声明了 viewport-fit=cover
[PASS] /admin 有 viewport meta
[PASS] /admin 声明了 viewport-fit=cover
[PASS] /recharge 有 viewport meta
[PASS] /recharge 声明了 viewport-fit=cover
[PASS] 账户列表 td 带 data-label
[PASS] 账户列表表头含车牌列
[PASS] 流水表 td 带 data-label
[PASS] CSS 有手机断点
[PASS] CSS 有表格卡片化规则
[PASS] CSS 有安全区适配
[PASS] CSS 统计卡用了 grid
[PASS] 输入框字号 ≥ 16px（防 iOS 自动缩放）
[PASS] 主按钮高度 ≥ 44px
[PASS] 输入框高度 ≥ 44px
[PASS] 竖排表单修正了 flex-basis 塌陷问题
[PASS] 车主端含局域网访问提示
---
340 / 340 通过
```
</details>

---

## 公网部署

### 为什么不用 GitHub Pages

**Pages 只托管静态文件。** 本项目是 Flask 动态应用，表单提交（`/entry`、`/pay`）
需要后端处理 —— Pages 跑不了，代码会被当文本返回，点提交直接 404。

这不是配置问题，是架构问题。

### 方案：GitHub Actions + Cloudflare 隧道

```
GitHub Actions runner（Ubuntu）
  ↓  pip install -r requirements.txt
  ↓  gunicorn app:app --bind 0.0.0.0:8080
  ↓  cloudflared tunnel --url http://localhost:8080
  ↓
https://xxxx-yyyy-zzz.trycloudflare.com   ← 手机直接打开
```

工作流文件：`.github/workflows/deploy.yml`

| 触发方式 | 说明 |
|---|---|
| 手动 | Actions 页面点 "Run workflow" |
| 推 tag | `git push origin v0.4` 自动触发 |

**代价**（说清楚比藏着好）：
- URL 每次运行都变（trycloudflare 的随机域名）
- 单次运行最长 6 小时
- Actions 免费额度有上限，滥用会触发限流

### 三个环境变量

| 变量 | 作用 | 不设会怎样 |
|---|---|---|
| `PORT` | 平台注入的端口 | 写死 3000 会让平台判定启动失败 |
| `SECRET_KEY` | session 加密密钥 | 随机生成，**重启后旧 session 失效** |
| `ACCESS_PWD` | 访问口令 | **完全公开**，任何人拿到链接都能改数据 |

> ⚠️ `SECRET_KEY` 原来写死在代码里（`parking-demo-secret-key`）。
> 公网上任何人都能用这个密钥伪造 session cookie —— 这是真实的安全漏洞，
> 现在改成环境变量读取、没配就随机生成。

### 访问口令

公网部署后必须加口令，否则后台页面谁都能进：

```python
@app.before_request
def check_access_password():
    if not ACCESS_PWD:
        return None                    # 没设口令 = 完全公开
    if session.get("access_ok"):
        return None                    # 已验证
    if request.path == "/access":
        return None                    # 口令页本身不能拦，否则死循环
    if request.path.startswith("/static/"):
        return None                    # 静态资源不能拦，否则样式加载不出来
    return redirect(url_for("access_page"))
```

### 生产级 WSGI 服务器

Flask 自带的开发服务器是**单线程**的，并发一多就卡。公网部署必须用：

| 环境 | 服务器 | 命令 |
|---|---|---|
| Linux（Actions / Render / Railway） | gunicorn | `gunicorn app:app --bind 0.0.0.0:$PORT` |
| Windows（本地） | waitress | `python app.py`（自动切换） |

`gunicorn` 依赖 Unix 的 `fcntl` 模块，**在 Windows 上装得上但跑不了**，
所以 `requirements.txt` 里两个都列了，本地和公网各用各的。

---

## 微信小程序

`miniprogram/` 目录是一套**原生微信小程序**，和 Flask 版共用同一套业务规则。

```
miniprogram/
├── app.js / app.json / app.wxss
├── utils/
│   └── parking-store.js      # ★ 数据层（业务规则）
└── pages/
    ├── index/                 # 车主端：入场 / 查费 / 缴费 / 车位总览
    ├── scan/                  # 扫码入场
    ├── recharge/              # 会员充值
    ├── remind/                # 催缴管理
    └── item-list/             # 进出场流水
```

### 数据层接口约定

数据层的方法签名和返回结构，跟微信云开发的 `createTableAccess` **完全一致**：

```javascript
list() / create() / update() / remove()   // 全部返回 { data, error }
                                          // 永远不 reject，错误用 error 对象表达
```

> ★ **这是刻意设计的**：现在用本地存储（`wx.setStorageSync`），
> 将来切云服务时**只替换 `utils/parking-store.js` 一个文件，
> 页面代码一行都不用改**。

### 当前状态：本地存储

| | 说明 |
|---|---|
| 数据位置 | 手机本地 `wx.setStorageSync` |
| 优点 | 不用服务器、不用备案，小程序能直接跑起来演示 |
| 代价 | 数据只在这台手机上，换手机看就是空的 |

> **微信云开发和「小程序认证 / 备案」是两件独立的事** ——
> 云开发决定数据存哪里，不需要你备案；
> 认证和 ICP 备案是微信平台要求你才能**正式发布上线**。
> 所以认证没过不影响写云开发代码，但会影响真机正式发布。

### 校验

小程序工程有 29 项 store 逻辑测试 + 静态检查（JSON 合法性、WXML 标签配对、
表达式限制、事件绑定方法存在性、JS 语法），全部通过。

---

## 版本规划

| 版本 | 状态 | 内容 |
|---|---|---|
| v0.1 | ✅ 已封版 | 临时停车：按小时 + 按日，每天封顶 40 |
| v0.2.1 | ✅ 已封版 | 扫码入场 + 车牌分流 + 账户管理 |
| v0.2.2 | ✅ 已封版 | 账户细分（月租/年租）+ 临时账号在场 + 独立充值页 |
| v0.3 | ✅ 已封版 | 手机端适配：表格卡片化 + iOS 缩放修复 + 安全区 + 触控目标 |
| v0.3.1 | ✅ 已封版 | 会员催缴（短信/微信模拟）+ 账户宽限期 + 过期出场注销 |
| **v0.4** | ✅ **当前** | **公网部署（Actions + 隧道）+ 访问口令 + 微信小程序** |
| v0.4 | 计划中 | 报表统计、CSV 导出、余额抵扣临停费用 |

---

## 技术栈

- **Python 3.13**（venv 隔离，除 Flask 外零依赖）
- **Flask 3.1** + **Jinja2** 服务端渲染
- **纯 CSS** 响应式布局（CSS Grid + 媒体查询，≤640px 表格转卡片）
- **JSON 文件持久化**（车位 / 停车流水 / 账户 / 充值流水 / 催缴记录 五张表）
- **Git 版本管理**

---

## 作者

**邹晔铖** · 齐齐哈尔大学 · 信息与计算科学 · 2027 届

---

## License

MIT
