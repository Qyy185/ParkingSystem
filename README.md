# 地下停车场管理系统（纯 Python 版）

> 项目位置：`C:\Users\36151\Desktop\01-项目代码\停车场管理系统-py\`
> **零 JavaScript**：没有 `.js` 文件，模板里没有 `<script>` 标签。
> 页面由 Python 的 Jinja2 模板渲染，交互靠 HTML 表单提交。

---

## 快速开始

### 1. 打开（PyCharm）

**File → Open** → 选文件夹：
```
C:\Users\36151\Desktop\01-项目代码\停车场管理系统-py
```

右下角确认显示 `Python 3.13 (parking-py)` 或类似（已自动配好 venv）。

### 2. 跑起来

**方式 A**：双击项目里的 `启动.bat`

**方式 B**：PyCharm 底部「终端」（`Alt + F12`）：
```bat
.venv\Scripts\python.exe app.py
```

**方式 C**：右键 `app.py` → Run 'app'

### 3. 看效果

| 页面 | 地址 |
|---|---|
| 车主端 | `http://localhost:3000` |
| 管理后台 | `http://localhost:3000/admin` |
| 真手机 | `http://172.21.95.217:3000`（手机连同一 WiFi） |

**应该能看到 16 个绿色格子**（A区 B区 各 8 个）。

---

## ⭐ 你的任务：3 个 TODO 函数

全在 **`parking.py`** 里。

| 状态 | 函数 | 干什么 |
|---|---|---|
| ✅ **你写完了** | `find_free_spot(spots)` | 找第一个空车位 |
| ✅ **你写完了** | `calc_fee(entryTime)` | 算钱 |
| ⬜ **待写** | `handle_entry(plate)` | 入场：分配车位、记时间、存数据、记流水 |
| ⬜ **待写** | `handle_exit(plate)` | 出场算钱（**先不放行**） |
| ⬜ **待写** | `handle_pay(plate)` | 缴费：释放车位、记流水 |

每个函数上面都写了：做什么、分几步、返回什么、**⚠️ 提示**。

### 骨架（填空自己填）

```python
def handle_entry(plate):
    spots = get_spots()
    spot = find_free_spot(spots)
    if spot is None:
        return {"ok": False, "msg": "车位已满"}

    # ↓↓↓ 这里填：改车位状态、存数据、记流水 ↓↓↓

    return {"ok": True, "spot": spot, "entryTime": spot["entryTime"]}
```

### 验证

```bat
.venv\Scripts\python.exe tools\selftest.py
```

期望 `12 / 12 通过`。

单个函数这样测：

```bat
.venv\Scripts\python.exe -c "import parking; print(parking.handle_entry('黑A12345'))"
```

---

## 🐍 这个项目会教你的东西

| 文件 | 你学到什么 |
|---|---|
| `app.py` | Flask 路由、**表单 POST/重定向/GET** 模式、Jinja2 模板渲染 |
| `parking.py` | 业务逻辑分层、字典操作、边界情况处理 |
| `store.py` | 文件读写、JSON 序列化、异常兜底 |
| `templates/` | **Jinja2 模板语法**：`{% for %}` 循环、`{{ }}` 变量、`{% if %}` 条件 |
| `static/` | 纯 CSS（响应式布局、Grid 布局） |

**⚠️ 三个 Python 语法要点**（你已经踩过）：

1. `def` / `if` / `for` 后面**必须有冒号**，函数体**缩进 4 空格**
2. 取字典值用 `spot["id"]`，**不是** `spot.id`
3. 判断空用 `is None`，**不是** `== None`

---

## 📂 目录结构

```
停车场管理系统-py/
├── 📄 app.py            ← Flask 服务（已通，不用改）
├── 🐍 parking.py        ← ★ 你的战场：3 个 TODO
├── 🐍 store.py          ← 数据读写（已通，不用改）
├── 📁 templates/        ← Jinja2 模板（已通，不用改）
│   ├── index.html         车主端
│   ├── admin.html         管理后台
│   └── error.html         404 页
├── 📁 static/
│   └── style.css       ← 纯 CSS
├── 📁 data/             ← 数据文件（自动生成）
├── 📁 tools/
│   ├── selftest.py     ← 自检（12 项）
│   └── convert_launcher.ps1
├── 📄 requirements.txt
├── 📄 启动.bat          ← 双击也能起服务
└── 📁 .venv/            ← 独立 Python 环境
```

---

## 🐍 Python vs JavaScript 速查

| 干什么 | JavaScript | Python |
|---|---|---|
| 打印 | `console.log(x)` | `print(x)` |
| 变量 | `let x = 1` | `x = 1` |
| 数组 | `const a = [1,2]` | `a = [1, 2]` |
| 取元素 | `a[0]` | `a[0]` |
| 长度 | `a.length` | `len(a)` |
| 加元素 | `a.push(x)` | `a.append(x)` |
| 取对象属性 | `o.id` | `o["id"]` ⚠️ |
| 函数 | `function f(x) { }` | `def f(x):` ⚠️**有冒号** |
| 判断 | `if (x) { }` | `if x:` ⚠️**没括号** |
| 遍历 | `arr.forEach(function(x){})` | `for x in arr:` |
| 空值 | `null` / `undefined` | `None` |
| 逻辑与/或 | `&&` / `\|\|` | `and` / `or` |
| 不等于 | `!==` | `!=` |
| 整除 | `Math.floor(a/b)` | `a // b` |
| 向上取整 | `Math.ceil(a)` | `import math; math.ceil(a)` |
| 注释 | `//` | `#` |
| 字符串拼接 | `'a' + b` | `f'a{b}'` 或 `str(b)` |
| 导模块 | `require('x')` | `import x` / `from x import y` |

---

## 💡 写不下去的时候

1. **看报错**：红色那几行，**最下面一行才是真正的错误**
2. **打印中间值**：在函数里加 `print("spots =", spots)`
3. **PyCharm 划波浪线不用管**——静态检查的抱怨，运行才是真的
4. **断点调试**：函数那行左侧点一下放红点，右键 → Debug，看左边变量面板

---

*环境已配好，12/12 自检通过，零 JS。开始写第一个函数吧。*
