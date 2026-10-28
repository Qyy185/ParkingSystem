# 地下停车场管理系统

> 一个用 **Python + Flask** 写的地下停车场管理系统，**零 JavaScript**。
> 车主用手机输入车牌就能入场、查费、缴费放行；管理员在电脑上看车位总览、流水和营收。

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**当前版本：v0.1**（临时停车场景 · 按小时/日计费）

---

## 这是什么

一个**完整可跑的小型业务系统**，覆盖停车场最核心的闭环：

```
入场 ──→ 分配车位 ──→ 在场计费 ──→ 缴费放行 ──→ 记账 ──→ 后台统计
```

**6 个核心业务函数全部独立实现**：

| 函数 | 职责 | 关键点 |
|---|---|---|
| `find_free_spot(spots)` | 车位分配 | 遍历找第一个 `status == "free"` 的 |
| `find_in_field(plate)` | 在场查询 | 归一化后比对，找不到返回 `None` |
| `calc_fee(entryTime)` | 分级计费 | 30 分钟免费 → 5 元/小时 → **每天**封顶 40 |
| `handle_entry(plate)` | 入场 | 校验车牌 + 查重 + 分配车位 + 记流水 |
| `handle_exit(plate)` | 出场计费 | **只算钱，不放行** |
| `handle_pay(plate)` | 缴费放行 | 释放车位 + 存盘 + 记流水 |

---

## 快速开始

```bash
# 1. 建虚拟环境
python -m venv .venv

# 2. 装依赖
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # macOS/Linux

# 3. 启动
.venv/Scripts/python.exe app.py
```

打开浏览器：

| 页面 | 地址 |
|---|---|
| 车主端 | `http://localhost:3000` |
| 管理后台 | `http://localhost:3000/admin` |
| 手机访问 | `http://<你的局域网IP>:3000` |

**Windows 用户可以直接双击 `启动.bat`。**

---

## 特色一：零 JavaScript

整个项目**没有一个 `.js` 文件，模板里没有一个 `<script>` 标签**。

```
浏览器提交表单 → Flask 收到 → 调用业务函数 → Jinja2 渲染整页 → 重定向回来
```

页面上的 16 个车位格子，是 Jinja2 循环渲染的：

```html
{% for s in spots %}
<div class="cell {{ 'is-free' if s.status == 'free' else 'is-busy' }}">
    <span class="cell-id">{{ s.id }}</span>
    <span class="cell-plate">{{ s.plate if s.plate else '空闲' }}</span>
</div>
{% endfor %}
```

**好处**：整个请求链路都在 Python 里，看得懂、追得完。
**代价**：每次提交整个页面刷新，交互体验不如 SPA。这是取舍，不是最优解。

---

## 特色二：两段式出场流程

`handle_exit` **只算钱，不释放车位**；`handle_pay` 才真正放行。

这对应真实停车场的流程，也给支付接口留了扩展路径：

```
handle_exit  →  算钱、返回金额、状态不变
handle_pay   →  用户确认付款后，才把车位释放
```

将来接微信支付，就是「算钱 → 用户付 → 放行」，两个函数都不用重写。

---

## 特色三：数据持久化与流水审计

车位状态存在 `data/parking.json`，进出场的每一条流水存在 `data/logs.json`。

```python
# store.py —— 异常兜底，文件坏了也不能让服务挂掉
def read_all():
    try:
        with open(FILE, "r", encoding="utf-8") as f:
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

---

## 业务规则

### 计费规则

| 停放时长 | 费用 | 说明 |
|---|---|---|
| 10 分钟 | 0 元 | 免费时段内 |
| 45 分钟 | 5 元 | 超出免费 30 分钟，向上取整为 1 小时 |
| 90 分钟 | 5 元 | 超出免费 60 分钟，计 1 小时 |
| 2 小时 | 10 元 | 超出免费 90 分钟，计 2 小时 |
| 8 小时 | 40 元 | 当日封顶 |
| 1 天 | 40 元 | 单日封顶 |
| 2 天 | 80 元 | **按天分段累加** |
| 3 天 | 120 元 | 按天分段累加 |

> ⚠️ **「每天封顶」和「总额封顶」是两种规则，差很多。**
> 停 3 天，总额封顶收 40 元；按天封顶收 40×3 = 120 元。
> 本项目实现的是**按天分段**：每天独立算「免费 30 分钟 → 整小时 → 当日封顶 40」，然后累加。

### 车牌规则

- **格式**：省份简称 + 字母 + 5~6 位（如 `黑A12345`、`京B12345`）
- **归一化**：大小写、空格、连字符、全角字符自动统一，
  所以 `黑a 12345` 和 `黑A12345` 是**同一辆车**
- **查重**：车已在场内时再次入场会被拒绝，并提示当前所在车位

### 三条业务约束

| 约束 | 实现位置 |
|---|---|
| 同一辆车不能占两个车位 | `handle_entry` 分配前查 `find_in_field` |
| 车牌格式非法直接拦截 | `handle_entry` 开头校验，`plate.py` 提供正则 |
| 缴费必须先算钱再清空 | `handle_pay` 先 `calc_fee` 再置 `None` |

> 💡 `get_spots()` 每次都会**从文件重新读**，返回全新的对象。
> 所以查重和改状态必须用同一个列表（`find_in_field_with_spots`），
> 否则 `write_all()` 会把没改过的旧数据写回去，车位永远释放不掉。

---

## 项目结构

```
.
├── app.py              # Flask 服务：路由 + 表单处理 + 模板过滤器
├── parking.py          # ★ 业务逻辑层（核心）
├── plate.py            # 车牌归一化与校验
├── store.py            # 数据读写层
├── templates/          # Jinja2 模板
│   ├── index.html      #   车主端
│   ├── admin.html      #   管理后台
│   └── error.html      #   404 页
├── static/
│   └── style.css       # 纯 CSS（响应式 + Grid 布局）
├── data/               # 运行时生成的数据文件（gitignore）
├── tools/
│   ├── selftest.py     # 55 项自动化自检
│   ├── launcher.src.txt # 启动器源文件（UTF-8）
│   └── convert_launcher.ps1  # 转码成 GBK 的 启动.bat
├── requirements.txt
└── 启动.bat
```

**分层设计**：

| 层 | 文件 | 职责 |
|---|---|---|
| 数据层 | `store.py` | 只读写文件，不含业务逻辑 |
| 业务层 | `parking.py` + `plate.py` | 纯逻辑，不碰 HTTP |
| 表现层 | `app.py` + `templates/` | 收请求、调逻辑、渲染页面 |

---

## 测试

```bash
.venv/Scripts/python.exe tools/selftest.py
```

自检分两部分，共 **55 项**：

**A. 业务规则单元测试**（直接调业务层，快，不依赖网络）

| 分组 | 覆盖内容 |
|---|---|
| 计费 | 10/30/45/90/120 分钟、8 小时、1/2/3 天封顶 |
| 车牌归一化 | 大小写、空格、连字符、全角、空输入 |
| 车牌校验 | 6 个合法车牌 + 7 个非法车牌 |
| 重复入场 | 首次成功、重复拒绝、大小写不同也算重复、只占 1 个车位 |
| 完整闭环 | 入场 → 查费 → **出场不放行** → 缴费 → 车位释放 → 流水两条 |
| 异常 | 不存在的车出场被拒绝 |

**B. HTTP 端到端测试**（真起服务器，请求页面和表单）

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
[PASS] 不存在的页面返回 404
---
55 / 55 通过
```

---

## 版本规划

| 版本 | 场景 | 计费方式 |
|---|---|---|
| **v0.1**（当前） | 商场临时停车 | 按小时 + 按日，每天封顶 40 |
| v0.2-a | 商场（完整版） | 临时停车为主 + 少量月租车位 |
| v0.2-b | 住宅小区 | 月租 / 年租为主，绑定固定车位 |

---

## 技术栈

- **Python 3.13**（venv 隔离，除 Flask 外零依赖）
- **Flask 3.1** + **Jinja2** 服务端渲染
- **纯 CSS** 响应式布局（CSS Grid，媒体查询适配手机）
- **JSON 文件持久化**
- **Git 版本管理**

---

## 作者

**邹晔铖** · 齐齐哈尔大学 · 信息与计算科学 · 2027 届

---

## License

MIT
