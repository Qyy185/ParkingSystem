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
import coupon
import mocks
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
# ★ v0.4.5：环境变量没设时，回退读项目根目录的 access-pwd.txt / admin-pwd.txt
#   （和公网部署的 deploy.yml 同一套优先级：环境变量 > 仓库文件 > 空）。
#   这样本地双击 启动.bat 也能用和公网一样的口令进管理后台，
#   而不是管理端被静默关闭、访问 /admin 被一声不吭弹回车主端。
#   想在本地彻底关闭口令：设环境变量 ACCESS_PWD=""（空字符串）即可，
#   显式设置会覆盖文件回退。
def _pwd_from_file(filename):
    """读项目根目录下的口令文件，读不到或为空返回空串。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read().strip()
    except OSError:
        return ""


if "ACCESS_PWD" in os.environ:
    ACCESS_PWD = os.environ.get("ACCESS_PWD", "").strip()
else:
    ACCESS_PWD = _pwd_from_file("access-pwd.txt")

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
if "ADMIN_PWD" in os.environ:
    ADMIN_PWD = os.environ.get("ADMIN_PWD", "").strip()
else:
    ADMIN_PWD = _pwd_from_file("admin-pwd.txt")

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
    # ★ v0.4.3：模拟功能清单也是管理端页面。
    #   它会暴露系统内部实现细节（哪些是假的、怎么换的、
    #   数据存在哪、认证怎么做的）—— 这些不该让普通车主看到。
    #   （2026-10-28 踩过：新增 /mocks 后忘了加进这个列表，
    #     车主直接访问能打开清单页。）
    "/mocks",
    # ★ v0.4.4：优惠券管理和收入报表也是管理端。
    #   发券、改券是运营动作；报表暴露全部门店的营收数字，
    #   都不该让普通车主看到。（教训同上：新增管理页第一件事先登记这里）
    "/coupons",
    "/report",
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

# ★ v0.4.3：模拟功能标注
#   把 mocks 模块整个注入模板，模板里就能用 {{ mocks.by_id('mock-pay') }}。
#   用 context_processor 而不是逐个 render_template 传参 ——
#   以后新增页面自动就有，不用记得补一行。
app.jinja_env.globals["mocks"] = mocks
app.jinja_env.filters["mock_level_label"] = mocks.level_label
app.jinja_env.filters["mock_level_desc"] = mocks.level_desc


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
        # ★ v0.4.5：把用户本来要去的页面带上，登录成功后直接回去，
        #   不再一律甩到后台首页。
        nxt = request.path if request.method == "GET" else ""
        if not ADMIN_PWD:
            # 没配管理员口令 → 管理端是关闭的，不是公开的
            return redirect(url_for("access_page",
                                    role=ROLE_ADMIN,
                                    next=nxt,
                                    err="管理后台未配置管理员口令，已关闭"))
        if role == ROLE_OWNER:
            # 已经以车主身份登录了，明确告诉他「你没权限」
            return redirect(url_for("access_page",
                                    role=ROLE_ADMIN,
                                    next=nxt,
                                    err="权限不足：管理后台需要管理员口令"))
        return redirect(url_for("access_page", role=ROLE_ADMIN, next=nxt))

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
        # next 可以走 query string 也可以走表单隐藏域
        nxt = request.args.get("next") or request.form.get("next") or ""

        if role == ROLE_ADMIN:
            if not ADMIN_PWD:
                error = "管理后台未配置管理员口令，已关闭"
            elif given == ADMIN_PWD:
                session["role"] = ROLE_ADMIN
                session["access_ok"] = True
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
                return redirect(nxt if nxt and nxt.startswith("/") else url_for("index"))
            else:
                error = "访问口令不对"

    return render_template(
        "access.html",
        error=error,
        role=role,
        has_owner_pwd=bool(ACCESS_PWD),
        has_admin_pwd=bool(ADMIN_PWD),
        next=request.args.get("next", ""),
        # ★ v0.4.5：这是面试演示项目，不是真实运营系统，
        #   口令直接印在页面上方便演示（手机、投屏都不用翻笔记）。
        #   ★ 如果哪天真要上线，删掉下面这两行即可，页面自动不显示提示。
        demo_owner_pwd=ACCESS_PWD,
        demo_admin_pwd=ADMIN_PWD,
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
#  模拟功能清单页（v0.4.3）
# ----------------------------------------------------------
#  为什么要有这个页面：
#   项目里有 7 处故意做成假的东西（不真扣钱、不真发短信、假摄像头……）。
#   面试时如果只说「这个是模拟的」，对方会觉得你只会做玩具。
#   但如果能主动指出「我知道哪几处是模拟的、每一处真实上线要换成什么、
#   大概要多少工作量」—— 这是**工程意识**，比假装全都实现了更有说服力。
#
#   所以这个页面把 mocks.py 里的登记表原样渲染出来，
#   并且每一处都写清三段：现在是什么 / 哪些是真的 / 换成什么。
#
#   ★ 这个页面属于管理端（要看到「不真扣钱」这些内部信息）。
@app.route("/mocks")
def mocks_page():
    """模拟功能清单：所有模拟点 + 真实上线时的替换方案。"""
    groups = mocks.grouped_by_level()
    counts = {
        "critical": len([m for m in mocks.MOCKS if m["level"] == "critical"]),
        "high": len([m for m in mocks.MOCKS if m["level"] == "high"]),
        "low": len([m for m in mocks.MOCKS if m["level"] == "low"]),
    }
    return render_template(
        "mocks.html",
        groups=groups,
        counts=counts,
        total=mocks.total(),
        levels=mocks.LEVELS,
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
    """出场：先算钱，再按情况分流（v0.4.4）。

    三条路：
      1. 费用 0（免费时段内 / 会员）→ 直接放行，不用进收银台
      2. 无感支付：账户开了 autopay 且余额够 → 自动扣款放行，跳过收银台
      3. 其余 → 进模拟收银台，人工确认支付

    ★ 顺序：无感扣款必须发生在 handle_pay **之前** ——
      handle_pay 会把临时账号删掉（会员账户保留），删完就没地方扣了。
    """
    raw = (request.form.get("plate") or "").strip()
    if not raw:
        return redirect(url_for("index", msg="车牌号不能为空"))

    result = parking.handle_exit(raw)
    if not result.get("ok"):
        return redirect(url_for("index", msg=result.get("msg", "查询失败")))

    fee = result["fee"]
    channel = result.get("channel", "temp")
    normalized = plate.normalize(raw)

    # ---- 路径 1：费用为 0，直接放行 ----
    if fee <= 0:
        pay_result = parking.handle_pay(normalized, method="免费放行")
        if not pay_result.get("ok"):
            return redirect(url_for("index", msg=pay_result.get("msg", "放行失败")))
        for k in ("pending_fee", "pending_minutes", "pending_channel"):
            session.pop(k, None)
        if channel == "member":
            return redirect(url_for("index", ok_msg="会员车辆出场，车位已释放（本次费用 0 元）"))
        return redirect(url_for("index", ok_msg="免费时段内出场，车位已释放（本次费用 0 元）"))

    # ---- 路径 2：无感支付（v0.4.4）----
    # 会员期内车辆费用本来就是 0，走不到这；能走到这的是
    # 过期会员（宽限期内）或其他开了 autopay 的账户 —— 按临停计费，
    # 余额够就自动扣，这正是真实系统里「免密扣款，出场秒抬杆」的体验。
    ready, acc = account.autopay_ready(normalized, fee)
    if ready:
        deduct = account.deduct(normalized, fee)
        if deduct.get("ok"):
            pay_result = parking.handle_pay(normalized, method="无感支付")
            if pay_result.get("ok"):
                for k in ("pending_fee", "pending_minutes", "pending_channel"):
                    session.pop(k, None)
                return redirect(url_for(
                    "index",
                    ok_msg=f"无感支付成功：已从余额扣费 {fee} 元"
                           f"（剩余 {deduct['balance']} 元），车位已释放"))

    # ---- 路径 3：进模拟收银台 ----
    # 把待缴费信息带到下一步。用 session 存比用 URL 参数安全。
    session["pending_plate"] = normalized
    session["pending_fee"] = result["fee"]
    session["pending_minutes"] = result["minutes"]
    session["pending_channel"] = channel
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
    autopay_selected = False
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
        # v0.4.4：充值时是否勾了「开通无感支付」
        autopay_selected = bool(session.get("pay_autopay"))
        if autopay_selected:
            subtitle += " · 开通无感支付"
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
            # v0.4.4：勾了「开通无感支付」就把开关带进充值
            want_autopay = bool(session.pop("pay_autopay", None))
            result = account.recharge(plate_no, pkg_kind, periods,
                                      autopay=want_autopay)
            if not result.get("ok"):
                return redirect(url_for("recharge", plate=plate_no, kind=pkg_kind,
                                        msg=result.get("msg", "充值失败")))
            # 清掉待支付信息
            for k in ("pay_plate", "pay_kind", "pay_periods"):
                session.pop(k, None)
            acc = result["account"]
            method_name = next((m["name"] for m in PAY_METHODS if m["id"] == method), "模拟支付")
            tail = "，已开通无感支付" if want_autopay else ""
            return redirect(url_for(
                "recharge", plate=plate_no, kind=pkg_kind,
                ok=f"充值成功 {result['amount']} 元（{method_name}），"
                   f"余额 {acc['balance']} 元，有效期至 {fmt_date(result['expiresAt'])}{tail}"))

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

            # ---- 优惠券（v0.4.4）----
            # 优惠码是表单传的，**必须在提交时重新校验**：
            # GET 渲染之后、POST 提交之前，券可能刚过期 / 刚被领完。
            # 顺带防止手改表单伪造更大的抵扣额 —— 抵扣额永远以后端算的为准。
            coupon_code = (request.form.get("coupon") or "").strip().upper()
            discount = 0
            if coupon_code:
                v = coupon.validate(coupon_code, pending, real_fee)
                if not v.get("ok"):
                    return redirect(url_for(
                        "index", msg="优惠码不可用：%s" % v.get("msg", "")))
                discount = v["discount"]

            method_name = next((m["name"] for m in PAY_METHODS if m["id"] == method), "模拟支付")
            result = parking.handle_pay(pending, method=method_name,
                                        discount=discount)
            if not result.get("ok"):
                return redirect(url_for("index", msg=result.get("msg", "缴费失败")))

            # 缴费成功才核销券 —— 失败的话不能白扣人家的使用次数
            if coupon_code and discount:
                coupon.consume(coupon_code, pending, real_fee, discount)

            for k in ("pending_fee", "pending_minutes", "pending_channel"):
                session.pop(k, None)
            if result.get("channel") == "member":
                return redirect(url_for("index", ok_msg="会员车辆出场，车位已释放（本次费用 0 元）"))
            if discount:
                return redirect(url_for(
                    "index",
                    ok_msg=f"缴费成功 {result['fee']} 元（{method_name}，"
                           f"优惠码已抵扣 {discount} 元，实收 {result['fee'] - discount} 元），车位已释放"))
            return redirect(url_for("index",
                                    ok_msg=f"缴费成功 {result['fee']} 元（{method_name}），车位已释放"))

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
        autopay_selected=autopay_selected,
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


@app.route("/accounts/autopay", methods=["POST"])
def account_autopay():
    """开通 / 关闭无感支付（v0.4.4）。

    开通后：这辆车出场时费用自动从余额扣（余额够才行），跳过收银台。
    """
    raw = (request.form.get("plate") or "").strip()
    enable = request.form.get("enable") == "1"
    result = account.set_autopay(raw, enable)
    if not result.get("ok"):
        return redirect(url_for("accounts", msg=result.get("msg", "操作失败")))
    word = "已开通" if enable else "已关闭"
    return redirect(url_for(
        "accounts", ok=f"{result['account']['plate']} {word}无感支付"))


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
    # ★ v0.4.4：充值时可顺带开通无感支付（勾选框）
    session["pay_autopay"] = request.form.get("autopay") == "1"

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
#  优惠券管理（v0.4.4）
# ----------------------------------------------------------
# 管理员发券 → 临停缴费时输入优惠码抵扣。
# 业务逻辑全部在 coupon.py，这里只管表单和跳转。

@app.route("/coupons")
def coupons_page():
    """优惠券管理页：发券表单 + 全部券的列表。"""
    return render_template(
        "coupons.html",
        coupons=sorted(coupon.all_coupons(),
                       key=lambda c: c.get("createdAt", 0), reverse=True),
        stats=coupon.stats(),
        msg=request.args.get("msg", ""),
        ok_msg=request.args.get("ok", ""),
    )


@app.route("/coupons/create", methods=["POST"])
def coupons_create():
    """发一张新券。"""
    result = coupon.create(
        request.form.get("code") or "",
        request.form.get("discount") or 0,
        request.form.get("maxUses") or 1,
        request.form.get("validDays") or 30,
    )
    if not result.get("ok"):
        return redirect(url_for("coupons_page", msg=result.get("msg", "发券失败")))
    cp = result["coupon"]
    return redirect(url_for(
        "coupons_page",
        ok=f"已发放优惠码 {cp['code']}（抵 {cp['discount']} 元，"
           f"限 {cp['maxUses']} 次用）"))


@app.route("/coupons/toggle", methods=["POST"])
def coupons_toggle():
    """停用 / 恢复一张券。"""
    code = request.form.get("code") or ""
    enable = request.form.get("enable") == "1"
    result = coupon.set_active(code, enable)
    if not result.get("ok"):
        return redirect(url_for("coupons_page", msg=result.get("msg", "操作失败")))
    word = "已恢复" if enable else "已停用"
    return redirect(url_for("coupons_page", ok=f"优惠码 {code} {word}"))


@app.route("/coupons/delete", methods=["POST"])
def coupons_delete():
    """删除一张没被用过的券（用过的删不掉，留着对账）。"""
    code = request.form.get("code") or ""
    result = coupon.remove(code)
    if not result.get("ok"):
        return redirect(url_for("coupons_page", msg=result.get("msg", "删除失败")))
    return redirect(url_for("coupons_page", ok=f"已删除优惠码 {code}"))


# ----------------------------------------------------------
#  收入报表（v0.4.4）
# ----------------------------------------------------------
# 真实停车系统的后台都有对账模块。这里按天分三列：
#   · 临停收入  —— logs.json 里 type=pay 的流水（fee 是实收，discount 是让利）
#   · 充值收入 —— recharges.json 里每笔充值的金额
#   · 优惠让利 —— 缴费流水里 discount 字段的合计
#
# ★ 口径说明：临停收入的 fee 记的是**折前金额**，实收 = fee - discount。
#   报表两列分开摆，总收入按实收算 —— 这是对账的基本要求。

REPORT_DAYS = (7, 30)


@app.route("/report")
def report():
    """收入报表：按天统计临停收入、充值收入、优惠让利。"""
    try:
        days = int(request.args.get("days") or 7)
    except ValueError:
        days = 7
    if days not in REPORT_DAYS:
        days = 7

    # 按天聚合
    by_day = {}          # 'YYYY-MM-DD' -> dict
    today = time.strftime("%Y-%m-%d")

    def bucket(stamp):
        return by_day.setdefault(stamp, {"parking": 0, "parkingCount": 0,
                                         "discount": 0, "recharge": 0,
                                         "rechargeCount": 0})

    for log in store.read_logs():
        if log.get("type") != "pay":
            continue
        stamp = time.strftime("%Y-%m-%d", time.localtime(log.get("time", 0) / 1000))
        b = bucket(stamp)
        b["parking"] += int(log.get("fee") or 0)
        b["parkingCount"] += 1
        b["discount"] += int(log.get("discount") or 0)

    for r in store.read_recharges():
        stamp = time.strftime("%Y-%m-%d", time.localtime(r.get("time", 0) / 1000))
        b = bucket(stamp)
        b["recharge"] += int(r.get("amount") or 0)
        b["rechargeCount"] += 1

    # 只取最近 days 天（含今天），旧数据不展示但参与累计
    rows = []
    for i in range(days):
        d = time.strftime("%Y-%m-%d", time.localtime(store.now_ms() / 1000 - i * 86400))
        b = bucket(d)
        paid = b["parking"] - b["discount"]          # 临停实收
        rows.append({
            "date": d,
            "isToday": d == today,
            "parking": b["parking"],
            "parkingPaid": paid,
            "parkingCount": b["parkingCount"],
            "discount": b["discount"],
            "recharge": b["recharge"],
            "rechargeCount": b["rechargeCount"],
            "total": paid + b["recharge"],
        })

    # 柱状图的缩放基准：区间内最大的单日收入
    max_total = max([r["total"] for r in rows], default=0)

    # 累计口径（全部历史，不限 days）
    all_parking = sum(int(l.get("fee") or 0) for l in store.read_logs()
                      if l.get("type") == "pay")
    all_discount = sum(int(l.get("discount") or 0) for l in store.read_logs()
                       if l.get("type") == "pay")
    all_recharge = sum(int(r.get("amount") or 0) for r in store.read_recharges())

    summary = {
        "today": rows[0]["total"] if rows else 0,
        "todayParking": rows[0]["parkingPaid"] if rows else 0,
        "todayRecharge": rows[0]["recharge"] if rows else 0,
        "range": sum(r["total"] for r in rows),
        "allParking": all_parking - all_discount,
        "allDiscount": all_discount,
        "allRecharge": all_recharge,
        "all": (all_parking - all_discount) + all_recharge,
    }

    return render_template(
        "report.html",
        rows=rows,
        days=days,
        max_total=max_total,
        summary=summary,
    )


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
