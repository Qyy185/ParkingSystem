"""
Flask 服务端
============

这个项目是「纯 Python」的：没有 app.js、没有 admin.js，
所有页面由 Python 的模板引擎渲染，所有交互靠 HTML 表单提交。

启动：python app.py

公网部署（v0.4）：
    PORT          —— 平台注入的端口，缺失则用 3000
    SECRET_KEY    —— session 密钥，缺失则随机生成（重启后旧 session 失效）
    ACCESS_PWD    —— 访问口令。设了它，所有页面都要先输入口令才能进。
                     不设 = 完全公开（本地开发默认）
"""

import os
import secrets
import socket
import time

from flask import Flask, redirect, render_template, request, session, url_for

import account
import notify
import parking
import plate
import remind
import store

app = Flask(__name__)

# ---------------------------------------------------------
#  配置（v0.4：适配公网部署）
# ---------------------------------------------------------

# ★ session 密钥：从环境变量读，读不到就随机生成。
#   原来写死 "parking-demo-secret-key" —— 公网上任何人都能用这个密钥
#   伪造 session cookie 冒充别人。随机生成就解决这个问题。
_SECRET = os.environ.get("SECRET_KEY")
if not _SECRET:
    _SECRET = secrets.token_hex(32)
app.secret_key = _SECRET

# 端口：PaaS 平台（Render / Railway / Heroku）会注入 PORT 环境变量。
# 写死 3000 的话，平台会以为你的服务没监听它给的端口，直接判定启动失败。
PORT = int(os.environ.get("PORT", "3000"))

# 访问口令：不设就是完全公开。面试演示时设上，防止被人刷数据。
ACCESS_PWD = os.environ.get("ACCESS_PWD", "").strip()

# ★ 管理员口令（v0.4.2）：车主端和管理后台分成两套权限。
#   ACCESS_PWD  → 车主端（入场、出场、给自己充值）
#   ADMIN_PWD   → 管理后台（看营收、改账户、发催缴）
#
#   为什么必须分开：原来只有一个口令，进了系统就能进 /admin 改所有数据。
#   公网上只要口令泄露（或者被人瞄到），整个后台就门户大开。
#   现在车主口令和管理员口令互不相通，车主进不去后台。
#
#   ★ 管理员口令不能为空 —— 宁可让后台进不去，也不能让后台没有保护。
#     没配 ADMIN_PWD 时，管理端直接关闭（访问会跳回并提示未授权）。
ADMIN_PWD = os.environ.get("ADMIN_PWD", "").strip()

# 角色常量
ROLE_OWNER = "owner"   # 车主
ROLE_ADMIN = "admin"   # 管理员

# ★ 管理端页面清单 —— 这些路径必须是管理员身份才能进。
#
#   注意：/accounts 和 /remind 也是管理端！
#   账户管理能改别人的会员、催缴能给任何人发消息，
#   它们和 /admin 是同一类东西，不能漏在外面。
ADMIN_PATHS = (
    "/admin",
    "/accounts",
    "/remind",
)


def is_admin_path(path):
    """判断这个路径是不是管理端。

    用前缀匹配：/admin、/accounts/add、/remind/send/one 都要拦住。
    """
    for p in ADMIN_PATHS:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def fmt_time(ts):
    """毫秒时间戳 → '2026-10-26 13:45:53'。

    后台流水表直接渲染 log.time 会显示成一串数字（1792993553950），
    人根本没法看。这里统一格式化。
    """
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts / 1000))
    except (TypeError, ValueError, OSError):
        return ""


def fmt_date(ts):
    """毫秒时间戳 → '2026-11-25'。用于会员有效期展示。"""
    if not ts:
        return "永久有效"
    try:
        return time.strftime("%Y-%m-%d", time.localtime(ts / 1000))
    except (TypeError, ValueError, OSError):
        return ""


def fmt_duration(minutes):
    """分钟 → '2 小时 15 分钟'。用于流水表的时长列。"""
    if minutes is None:
        return ""
    minutes = int(minutes)
    if minutes < 60:
        return "%d 分钟" % minutes
    h, m = divmod(minutes, 60)
    return "%d 小时 %d 分钟" % (h, m) if m else "%d 小时" % h


# 把格式化函数注册给模板，模板里直接用 {{ log.time | fmt_time }}
app.jinja_env.filters["fmt_time"] = fmt_time
app.jinja_env.filters["fmt_date"] = fmt_date
app.jinja_env.filters["fmt_duration"] = fmt_duration
app.jinja_env.filters["kind_label"] = account.kind_label
app.jinja_env.filters["channel_label"] = notify.channel_label
app.jinja_env.filters["in_grace"] = account.in_grace
app.jinja_env.filters["grace_until"] = account.grace_until


# ----------------------------------------------------------
#  局域网地址（手机访问用）
# ----------------------------------------------------------
def lan_ip():
    """拿到本机的局域网 IP，让用户知道手机该输哪个地址。

    做法是建一个 UDP socket 连一个「不会真的发包」的地址：
    connect() 不会发送任何数据，但操作系统会按路由表选出
    「走默认网卡的那个出口地址」，那正是手机能访问到的 IP。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        ip = ""
    finally:
        s.close()
    return ip if ip.startswith(("192.168.", "10.", "172.")) else ""


# ----------------------------------------------------------
#  访问口令（v0.4 → v0.4.2 拆成两套）
# ----------------------------------------------------------
# 公网部署后，任何拿到链接的人都能进后台乱改数据。
# 设一个口令，进页面先要输入，验证过就记在 session 里。
#
# ★ v0.4.2：车主端和管理后台分成两个网页、两套口令。
#   · 车主口令 ACCESS_PWD  → 车主端（入场、出场、给自己充值）
#   · 管理员口令 ADMIN_PWD  → 管理后台（营收、账户管理、催缴）
#   两套口令互不相通，车主拿到车主口令也进不了后台。
#
# 不设 ACCESS_PWD = 车主端完全公开，本地开发不受影响。
#   但 ADMIN_PWD 不设的话，管理端是**关闭**的（不是公开）——
#   没保护的后台比没有后台更危险。

@app.before_request
def check_access_password():
    """每个请求前检查身份。

    放行的情况：
      · 静态资源 → 放行（否则页面样式都加载不出来）
      · 口令页本身 → 放行（不然死循环）
      · 访问的是管理端，但当前身份是管理员 → 放行
      · 访问的是管理端，但当前身份是车主 → 拦下，提示权限不足
      · 没设车主口令（本地开发）→ 放行
      · 已经用车主口令验证过 → 放行
    """
    # 静态资源永远放行
    if request.path.startswith("/static/"):
        return None

    # 口令页本身不能拦，否则死循环
    if request.path == "/access":
        return None

    role = session.get("role")

    # ★ 管理端：必须是管理员
    if is_admin_path(request.path):
        if role == ROLE_ADMIN:
            return None
        if not ADMIN_PWD:
            # 没配管理员口令 → 管理端是关闭的，不是公开的
            return redirect(url_for("access_page",
                                    role=ROLE_ADMIN,
                                    err="管理后台未配置管理员口令，已关闭"))
        if role == ROLE_OWNER:
            # 已经以车主身份登录了，明确告诉他「你没权限」
            return redirect(url_for("access_page",
                                    role=ROLE_ADMIN,
                                    err="权限不足：管理后台需要管理员口令"))
        return redirect(url_for("access_page", role=ROLE_ADMIN))

    # ★ 车主端
    if not ACCESS_PWD:
        return None
    if role in (ROLE_OWNER, ROLE_ADMIN):
        return None
    return redirect(url_for("access_page", role=ROLE_OWNER))


@app.route("/access", methods=["GET", "POST"])
def access_page():
    """口令输入页。

    ★ v0.4.2：支持两种身份，用 `role` 参数区分。
      · role=owner  → 车主端（默认）
      · role=admin  → 管理后台

    管理员口令通过后，管理员能进管理端，也能进车主端
    （管理员自己也得停车）。
    """
    # 没设车主口令就不用输口令；没设管理员口令则管理端关闭
    if not ACCESS_PWD and not ADMIN_PWD:
        return redirect(url_for("index"))

    role = request.args.get("role") or request.form.get("role") or ROLE_OWNER
    if role not in (ROLE_OWNER, ROLE_ADMIN):
        role = ROLE_OWNER

    error = request.args.get("err", "")

    if request.method == "POST":
        given = (request.form.get("pwd") or "").strip()

        if role == ROLE_ADMIN:
            if not ADMIN_PWD:
                error = "管理后台未配置管理员口令，已关闭"
            elif given == ADMIN_PWD:
                session["role"] = ROLE_ADMIN
                session["access_ok"] = True
                nxt = request.args.get("next")
                return redirect(nxt if nxt and nxt.startswith("/") else url_for("admin"))
            else:
                error = "管理员口令不对"
        else:
            if not ACCESS_PWD:
                # 没设车主口令 = 本地开发，直接进
                session["role"] = ROLE_OWNER
                session["access_ok"] = True
                return redirect(url_for("index"))
            if given == ACCESS_PWD:
                session["role"] = ROLE_OWNER
                session["access_ok"] = True
                nxt = request.args.get("next")
                return redirect(nxt if nxt and nxt.startswith("/") else url_for("index"))
            else:
                error = "访问口令不对"

    return render_template(
        "access.html",
        error=error,
        role=role,
        has_owner_pwd=bool(ACCESS_PWD),
        has_admin_pwd=bool(ADMIN_PWD),
    )


@app.route("/logout")
def logout():
    """退出登录，回到口令页。"""
    session.clear()
    return redirect(url_for("access_page", role=ROLE_OWNER))


# ----------------------------------------------------------
#  页面
# ----------------------------------------------------------
@app.route("/")
def index():
    """车主端：看车位、入场、出场算钱、缴费。"""
    spots = parking.get_spots()
    free_count = sum(1 for s in spots if s["status"] == "free")
    occupied_count = len(spots) - free_count
    total = len(spots) or 1
    # 提示信息放在 URL 查询参数里，模板直接用 request.args 取。
    # （不要用 error / status 这种名字，Jinja2 和 Flask 内部有冲突）
    from flask import session
    return render_template(
        "index.html",
        spots=spots,
        free_count=free_count,
        occupied_count=occupied_count,
        used_percent=round(occupied_count * 100.0 / total, 1),
        free_percent=round(free_count * 100.0 / total, 1),
        msg=request.args.get("msg", ""),
        ok_msg=request.args.get("ok", ""),
        fee=request.args.get("fee", ""),
        minutes=request.args.get("minutes", ""),
        fee_plate=session.get("pending_plate", "") or "",
        lan_ip=lan_ip(),
        port=PORT,
    )


@app.route("/admin")
def admin():
    """管理后台：车位总览 + 流水 + 营收。"""
    spots = parking.get_spots()
    logs = store.read_logs()
    free_count = sum(1 for s in spots if s["status"] == "free")
    occupied_count = len(spots) - free_count
    total = len(spots) or 1
    return render_template(
        "admin.html",
        spots=spots,
        logs=list(reversed(logs)),
        free_count=free_count,
        occupied_count=occupied_count,
        used_percent=round(occupied_count * 100.0 / total, 1),
        free_percent=round(free_count * 100.0 / total, 1),
        revenue=store.today_revenue(),
    )


# ----------------------------------------------------------
#  表单处理（车主端的 4 个操作）
# ----------------------------------------------------------
@app.route("/entry", methods=["POST"])
def entry():
    """入场：登记车牌，系统分配车位。"""
    raw = (request.form.get("plate") or "").strip()
    if not raw:
        return redirect(url_for("index", msg="车牌号不能为空"))

    # 车牌格式校验，放在业务层之前拦一道，给用户更明确的提示
    if not plate.is_valid(raw):
        return redirect(url_for("index", msg=plate.invalid_reason(raw)))

    result = parking.handle_entry(raw)
    if not result.get("ok"):
        return redirect(url_for("index", msg=result.get("msg", "入场失败")))

    spot = result["spot"]
    # 分流结果也告诉用户：这辆车按月租/年租还是临停处理的
    channel = result.get("channel")
    if channel == "member":
        kind_label = account.kind_label(result.get("kind"))
        tail = f"（{kind_label}，不计停车费）"
    else:
        tail = "（临停，按小时计费）"
    return redirect(url_for(
        "index", ok_msg=f"入场成功，车位 {spot['id']} {tail}"))


@app.route("/exit", methods=["POST"])
def exit():
    """出场：先算钱，不放行车位。

    ★ v0.4.2：算完钱**不直接放行**，跳到模拟支付确认页。
      车主在确认页看清楚金额、选支付方式、点「确认支付」才真正出场。
    """
    raw = (request.form.get("plate") or "").strip()
    if not raw:
        return redirect(url_for("index", msg="车牌号不能为空"))

    result = parking.handle_exit(raw)
    if not result.get("ok"):
        return redirect(url_for("index", msg=result.get("msg", "查询失败")))

    # 把待缴费信息带到下一步。用 session 存比用 URL 参数安全。
    from flask import session
    session["pending_plate"] = plate.normalize(raw)
    session["pending_fee"] = result["fee"]
    session["pending_minutes"] = result["minutes"]
    session["pending_channel"] = result.get("channel", "temp")
    # ★ 跳模拟支付页，而不是直接缴费
    return redirect(url_for("pay_confirm", kind="parking"))


@app.route("/pay", methods=["POST"])
def pay():
    """缴费：确认收款，释放车位。

    ★ v0.4.2：这个路由现在是**从模拟支付页点「确认支付」后**调用的，
      不再是车主端那个「确认缴费」按钮的直接目标。
      （车主端现在只展示费用，缴费必须经过 /pay/confirm 确认页）
    """
    from flask import session
    pending = (session.pop("pending_plate", "") or "").strip()
    if not pending:
        return redirect(url_for("index", msg="没有待缴费的车辆"))

    result = parking.handle_pay(pending)
    if not result.get("ok"):
        return redirect(url_for("index", msg=result.get("msg", "缴费失败")))

    # 会员车位费用为 0，但也是「出场了」，分开说清楚
    if result.get("channel") == "member":
        return redirect(url_for("index", ok_msg="会员车辆出场，车位已释放（本次费用 0 元）"))
    return redirect(url_for("index", ok_msg="缴费成功，车位已释放"))


# ----------------------------------------------------------
#  模拟支付确认页（v0.4.2）
# ----------------------------------------------------------
# 为什么要加这一层：
#   原来充值和缴费都是「点一下按钮就直接扣款/放行」，
#   真实场景里不可能这样 —— 支付前得有确认页，
#   让用户看清金额、选支付方式、确认后才生效。
#   这一层也是接真支付接口（微信/支付宝）时唯一要改的地方。
#
# 两种业务共用这一个页面：
#   kind=recharge  会员充值（车牌 + 套餐 + 金额）
#   kind=parking   停车缴费（车牌 + 停放时长 + 金额）
#
# ★ 关键设计：金额**不接受前端传参**。
#   确认页展示的金额是后端算好的，存在 session 里；
#   提交时后端重新算一遍，对不上就报错。
#   否则改一下表单里的 amount 就能 1 元充 1000 元。

# 模拟的支付方式（真接支付时换成实际渠道）
PAY_METHODS = [
    {"id": "wechat", "name": "微信支付", "tag": "模拟"},
    {"id": "alipay", "name": "支付宝", "tag": "模拟"},
    {"id": "cash", "name": "现金", "tag": "线下"},
]


@app.route("/pay/confirm", methods=["GET", "POST"])
def pay_confirm():
    """模拟收银台：确认金额 + 选支付方式 + 确认支付。

    GET  → 显示确认页
    POST → 校验金额 → 执行（充值或缴费）→ 跳回结果页
    """
    from flask import session

    kind = request.values.get("kind") or ""
    if kind not in ("recharge", "parking"):
        return redirect(url_for("index", msg="支付类型不对"))

    method = request.values.get("method") or "wechat"

    # ---------------------------------------------------------
    #  取待支付的信息
    # ---------------------------------------------------------
    if kind == "recharge":
        plate_no = (session.get("pay_plate") or "").strip()
        pkg_kind = session.get("pay_kind") or ""
        periods = int(session.get("pay_periods") or 0)
        option = account.get_option(pkg_kind, periods) if pkg_kind else None
        if not plate_no or option is None:
            return redirect(url_for("recharge", msg="支付信息已失效，请重新选择套餐"))
        amount = option["price"]
        title = "会员充值"
        subtitle = option["label"]
    else:
        plate_no = (session.get("pending_plate") or "").strip()
        amount = session.get("pending_fee")
        minutes = session.get("pending_minutes")
        channel = session.get("pending_channel") or "temp"
        if not plate_no or amount is None:
            return redirect(url_for("index", msg="没有待缴费的车辆，请重新查询"))
        title = "停车缴费"
        subtitle = "停放 %s 分钟" % fmt_duration(minutes) if minutes is not None else ""
        if channel == "member":
            subtitle += " · 会员车辆本次费用 0 元"

    # ---------------------------------------------------------
    #  POST：真正执行
    # ---------------------------------------------------------
    if request.method == "POST":
        if kind == "recharge":
            # ★ 后端重算金额，不信前端传的
            real_option = account.get_option(pkg_kind, periods)
            if real_option is None or real_option["price"] != amount:
                return redirect(url_for("recharge", plate=plate_no, kind=pkg_kind,
                                        msg="支付信息已失效，请重新选择套餐"))
            result = account.recharge(plate_no, pkg_kind, periods)
            if not result.get("ok"):
                return redirect(url_for("recharge", plate=plate_no, kind=pkg_kind,
                                        msg=result.get("msg", "充值失败")))
            # 清掉待支付信息
            for k in ("pay_plate", "pay_kind", "pay_periods"):
                session.pop(k, None)
            acc = result["account"]
            method_name = next((m["name"] for m in PAY_METHODS if m["id"] == method), "模拟支付")
            return redirect(url_for(
                "recharge", plate=plate_no, kind=pkg_kind,
                ok=f"充值成功 {result['amount']} 元（{method_name}），"
                   f"余额 {acc['balance']} 元，有效期至 {fmt_date(result['expiresAt'])}"))

        else:
            # 停车缴费：后端重算费用（停久了会变，但这里取的是同一笔）
            pending = (session.pop("pending_plate", "") or "").strip()
            if not pending:
                return redirect(url_for("index", msg="没有待缴费的车辆"))
            spots, spot = parking.find_in_field_with_spots(pending)
            if spot is None:
                return redirect(url_for("index", msg="这辆车已经出场了"))
            real_fee = 0 if (spot.get("channel") or "temp") == "member" else parking.calc_fee(spot["entryTime"])
            if real_fee != amount:
                return redirect(url_for("index", msg="费用已变化，请重新查询"))
            result = parking.handle_pay(pending)
            if not result.get("ok"):
                return redirect(url_for("index", msg=result.get("msg", "缴费失败")))
            for k in ("pending_fee", "pending_minutes", "pending_channel"):
                session.pop(k, None)
            method_name = next((m["name"] for m in PAY_METHODS if m["id"] == method), "模拟支付")
            if result.get("channel") == "member":
                return redirect(url_for("index", ok_msg="会员车辆出场，车位已释放（本次费用 0 元）"))
            return redirect(url_for("index", ok_msg=f"缴费成功 {result['fee']} 元（{method_name}），车位已释放"))

    # ---------------------------------------------------------
    #  GET：显示确认页
    # ---------------------------------------------------------
    return render_template(
        "pay_confirm.html",
        kind=kind,
        title=title,
        subtitle=subtitle,
        plate=plate_no,
        amount=amount,
        methods=PAY_METHODS,
        method=method,
    )


# ----------------------------------------------------------
#  扫码入场（v0.2.1）
# ----------------------------------------------------------
# 模拟现实里的车牌识别摄像头：
#   扫到车牌 → 查账户 → 知道这车走月租还是临停 → 放行
# 零 JavaScript，全部靠表单 POST 串联。

@app.route("/scan")
def scan():
    """扫码页：取景框 + 模拟扫描按钮。"""
    return render_template(
        "scan.html",
        detected=request.args.get("plate", ""),
        detected_kind=request.args.get("kind", ""),
        detected_label=request.args.get("label", ""),
        msg=request.args.get("msg", ""),
        ok_msg=request.args.get("ok", ""),
        lan_ip=lan_ip(),
        port=PORT,
    )


@app.route("/scan/detect", methods=["POST"])
def scan_detect():
    """模拟摄像头识别。

    现实中这一步是 OCR 直接读出车牌。现在没有摄像头，
    所以改成跳到一个「摄像头看到了什么」的输入框 —— 等价于手动报车牌。

    ★ 分流判断（查账户 → 月租/临停）是在 scan_confirm 里做的，是真的逻辑。
    """
    return render_template(
        "scan.html",
        detected="",
        detected_kind="",
        detected_label="",
        msg="",
        ok_msg="",
        capturing=True,
    )


@app.route("/scan/confirm", methods=["POST"])
def scan_confirm():
    """确认入场：按车牌分流，然后走正常入场流程。"""
    raw = (request.form.get("plate") or "").strip()
    if not raw:
        return redirect(url_for("scan", msg="车牌号不能为空"))
    if not plate.is_valid(raw):
        return redirect(url_for("scan", msg=plate.invalid_reason(raw)))

    # 扫到牌 → 查账户 → 分流
    kind, acc = account.classify(raw)
    is_member = kind in (account.MONTHLY, account.YEARLY)
    if is_member:
        label = account.kind_label(kind)
    elif acc is not None and acc.get("kind") == account.TEMPORARY:
        label = "临停（在场）"
    else:
        label = "临停（未登记）"

    result = parking.handle_entry(raw)
    if not result.get("ok"):
        return redirect(url_for("scan", msg=result.get("msg", "入场失败")))

    spot = result["spot"]
    channel = result.get("channel")
    if channel == "member":
        tip = f"{account.kind_label(kind)}入场，车位 {spot['id']}，本次不计停车费"
    else:
        tip = f"临停车入场，车位 {spot['id']}"
    return redirect(url_for("scan", plate=plate.normalize(raw),
                            kind=channel, label=label, ok=tip))


# ----------------------------------------------------------
#  账户管理（v0.2.1）
# ----------------------------------------------------------
# 账户 = 车牌 + 类型标记。只记录月租 / 临停，不涉及付款。

@app.route("/accounts")
def accounts():
    """账户列表页。"""
    accounts_list = account.all_accounts()
    # 新注册的排前面
    accounts_list = sorted(accounts_list,
                           key=lambda a: a.get("createdAt", 0), reverse=True)
    recharges = list(reversed(store.read_recharges()))
    return render_template(
        "accounts.html",
        accounts=accounts_list,
        recharges=recharges,
        stats=account.stats(),
        kind_label=account.kind_label,
        is_expired=account.is_expired,
        msg=request.args.get("msg", ""),
        ok_msg=request.args.get("ok", ""),
    )


@app.route("/accounts/add", methods=["POST"])
def account_add():
    """登记账户。"""
    raw = (request.form.get("plate") or "").strip()
    kind = request.form.get("kind") or account.TEMPORARY
    holder = request.form.get("holder") or ""
    note = request.form.get("note") or ""
    phone_no = request.form.get("phone") or ""

    result = account.register(raw, kind, holder, note, phone_no=phone_no)
    if not result.get("ok"):
        return redirect(url_for("accounts", msg=result.get("msg", "登记失败")))

    acc = result["account"]
    word = "已登记" if result.get("created") else "已更新"
    return redirect(url_for(
        "accounts",
        ok=f"{word}账户 {acc['plate']}，类型 {account.kind_label(acc['kind'])}"))


@app.route("/accounts/toggle", methods=["POST"])
def account_toggle():
    """切换账户类型：月租 ↔ 临停。"""
    raw = (request.form.get("plate") or "").strip()
    kind = request.form.get("kind") or ""
    result = account.change_kind(raw, kind)
    if not result.get("ok"):
        return redirect(url_for("accounts", msg=result.get("msg", "操作失败")))
    return redirect(url_for(
        "accounts",
        ok=f"{result['account']['plate']} 已改为 {account.kind_label(kind)}"))


@app.route("/accounts/delete", methods=["POST"])
def account_delete():
    """删除账户。"""
    raw = (request.form.get("plate") or "").strip()
    result = account.remove(raw)
    if not result.get("ok"):
        return redirect(url_for("accounts", msg=result.get("msg", "删除失败")))
    return redirect(url_for("accounts", ok=f"已删除账户 {result['removed']['plate']}"))


# ----------------------------------------------------------
#  充值（v0.2.2）
# ----------------------------------------------------------
# 独立的充值页面，和停车费付款页面**分开**：
#   · 停车费付款 → /exit → /pay（车主端）
#   · 会员充值   → /recharge（独立页面）
# 但付款页面可以**跳过来**（车主端有入口链接）。

@app.route("/recharge")
def recharge():
    """充值页第一步：选套餐（带 kind 参数时同时显示第二步选时长）。"""
    plate_no = (request.args.get("plate") or "").strip()
    kind = request.args.get("kind") or ""
    if kind and not account.is_member_kind(kind):
        kind = ""

    current = None
    if plate_no:
        current = account.find_by_plate(plate_no)

    return render_template(
        "recharge.html",
        packages=account.PACKAGES,
        plate=plate_no,
        kind=kind,
        current=current,
        selected=None,
        account_plate="",
        msg=request.args.get("msg", ""),
        ok_msg=request.args.get("ok", ""),
    )


@app.route("/recharge/select", methods=["POST"])
def recharge_select():
    """充值页第二步：选时长档位 → 展示确认。

    同一个路由处理两种情况：
      · 没传 periods → 只选了套餐，显示「第二步·选时长」
      · 传了 periods → 套餐和档位都定了，显示「确认充值」
    """
    plate_no = (request.form.get("plate") or "").strip()
    kind = request.form.get("kind") or ""

    if not plate.is_valid(plate_no):
        return redirect(url_for("recharge", plate=plate_no,
                                msg=plate.invalid_reason(plate_no)))
    if not account.is_member_kind(kind):
        return redirect(url_for("recharge", plate=plate_no, msg="请选择月租或年租套餐"))

    try:
        periods = int(request.form.get("periods") or 0)
    except ValueError:
        periods = 0

    # 还没选档位 → 显示第二步
    if periods <= 0:
        return render_template(
            "recharge.html",
            packages=account.PACKAGES,
            plate=plate_no,
            kind=kind,
            current=account.find_by_plate(plate_no),
            selected=None,
            account_plate="",
            msg="",
            ok_msg="",
        )

    option = account.get_option(kind, periods)
    if option is None:
        return redirect(url_for("recharge", plate=plate_no, kind=kind,
                                msg="没有这个套餐档位"))

    selected = {
        "kind": kind,
        "periods": periods,
        "label": option["label"],
        "price": option["price"],
    }
    current = account.find_by_plate(plate_no)

    # ★ v0.4.2：不再直接扣款，改为跳模拟支付确认页。
    #   待支付信息放 session，确认页再取。
    from flask import session
    session["pay_plate"] = plate.normalize(plate_no)
    session["pay_kind"] = kind
    session["pay_periods"] = periods

    return redirect(url_for("pay_confirm", kind="recharge"))


@app.route("/recharge/do", methods=["POST"])
def recharge_do():
    """确认充值 —— ★ v0.4.2 已停用，这个路由现在只是个跳板。

    原来这里直接调 `account.recharge()` 扣款。
    加了模拟支付确认页之后，扣款应该发生在
    「确认页 → 点确认支付」那一步（/pay/confirm?kind=recharge）。

    ★ 为什么不能直接删掉这个路由：
    删了的话，浏览器里存的旧书签 / 旧页面表单 POST 过来会 404，
    用户看到的是「服务器错误」而不是「请重新操作」。
    留一个跳转 + 友好提示更稳妥。

    ★ 而且它现在**不能扣款**了 —— 只是把人引回充值页重新走流程。
    这很重要：不然它就是个绕过确认页的后门。
    """
    plate_no = (request.form.get("plate") or "").strip()
    kind = request.form.get("kind") or ""
    return redirect(url_for(
        "recharge", plate=plate_no, kind=kind,
        msg="请在支付确认页完成充值（直接提交不会扣款）"))


# ----------------------------------------------------------
#  催缴（v0.3.1）
# ----------------------------------------------------------
# 会员租期快到期时主动提醒续费。
#   · /remind            催缴管理页：谁要催、催了没、发送记录
#   · /remind/send       批量发送（可选渠道）
#   · /remind/send/one   单个账户发送
#   · /remind/contact    补手机号 / 微信号
#
# 消息当前走「模拟渠道」：不真发短信，但完整记录到
# data/notifications.json，页面上能看到「发给谁、发的是什么」。

@app.route("/remind")
def remind_page():
    """催缴管理页。"""
    try:
        within = int(request.args.get("days") or 7)
    except ValueError:
        within = 7
    if within not in (3, 7, 15, 30):
        within = 7

    pending = account.pending_reminders(within)
    notifications = list(reversed(store.read_notifications()))

    # 统计
    # 「待催」= 需要提醒的账户总数
    # 「未发送」= 这些账户里当前档位还没催过的（真正要动手的）
    # 「本档已催」= 当前档位已经催过、不用再发的
    stats = {
        "pending": len(pending),
        "unsent": len([p for p in pending if not p["reminded"]]),
        "sent": len([p for p in pending if p["reminded"]]),
        "noContact": len([p for p in pending if not p["targets"]]),
        "totalSent": len(notifications),
    }

    # 宽限期状态（v0.3.1）：不在待催列表里，但要在页面上说明白
    acc_stats = account.stats()
    stats["inGrace"] = acc_stats.get("inGrace", 0)
    stats["obsolete"] = acc_stats.get("obsolete", 0)

    return render_template(
        "remind.html",
        pending=pending,
        notifications=notifications,
        stats=stats,
        within=within,
        channels=notify.available_channels(),
        grace_days=account.GRACE_DAYS,
        msg=request.args.get("msg", ""),
        ok_msg=request.args.get("ok", ""),
    )


@app.route("/remind/send", methods=["POST"])
def remind_send():
    """批量发送催缴。"""
    channel_id = (request.form.get("channel") or "").strip() or None
    try:
        within = int(request.form.get("days") or 7)
    except ValueError:
        within = 7

    result = remind.send_all_reminders(within, channel_id)
    ok_msg = "催缴完成：发送 %d 个，跳过 %d 个（共 %d 个待催）" % (
        result["sent"], result["skipped"], result["total"])
    return redirect(url_for("remind_page", days=within, ok=ok_msg))


@app.route("/remind/send/one", methods=["POST"])
def remind_send_one():
    """给单个账户发催缴。"""
    raw = (request.form.get("plate") or "").strip()
    channel_id = (request.form.get("channel") or "").strip() or None
    force = request.form.get("force") == "1"

    result = remind.send_reminder(raw, channel_id, force)
    if result.get("ok"):
        ok_msg = "已给 %s 发送催缴（%d 渠道）" % (
            plate.normalize(raw), len(result.get("sent", [])))
        return redirect(url_for("remind_page", ok=ok_msg))
    return redirect(url_for("remind_page", msg=result.get("msg", "发送失败")))


@app.route("/remind/contact", methods=["POST"])
def remind_contact():
    """给账户补手机号 / 微信号。"""
    raw = (request.form.get("plate") or "").strip()
    phone_no = (request.form.get("phone") or "").strip()
    wechat_id = (request.form.get("wechat") or "").strip()

    if not phone_no and not wechat_id:
        return redirect(url_for("remind_page", msg="手机号和微信号至少填一个"))

    result = account.set_contact(raw, phone_no, wechat_id)
    if not result.get("ok"):
        return redirect(url_for("remind_page", msg=result.get("msg", "保存失败")))
    return redirect(url_for("remind_page", ok="已保存 %s 的联系方式" % result["account"]["plate"]))


# ----------------------------------------------------------
#  错误处理
# ----------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="没找到这个页面"), 404


if __name__ == "__main__":
    print("=" * 60)
    print("  地下停车场系统（纯 Python 版）已启动")
    print("=" * 60)
    print("  车主端:   http://localhost:%d/" % PORT)
    print("  扫码入场: http://localhost:%d/scan" % PORT)
    print("  充值:     http://localhost:%d/recharge" % PORT)
    print("  催缴:     http://localhost:%d/remind" % PORT)
    print("  账户管理: http://localhost:%d/accounts" % PORT)
    print("  管理后台: http://localhost:%d/admin" % PORT)
    if ACCESS_PWD:
        print("  访问口令: 已启用（不设 ACCESS_PWD 可关闭）")
    print("=" * 60)
    # ★ 优先用 waitress —— 纯 Python 实现，能在 Windows 上跑，8 线程并发。
    #   gunicorn 只在 Linux 上能用（公网部署环境基本都是 Linux），
    #   所以公网那边直接命令行 `gunicorn app:app` 启动，不走这个分支。
    try:
        from waitress import serve
        print("  运行模式: waitress（多线程）")
        serve(app, host="0.0.0.0", port=PORT, threads=8)
    except ImportError:
        print("  运行模式: Flask 开发服务器（单线程，仅限本地）")
        app.run(host="0.0.0.0", port=PORT, debug=False)
