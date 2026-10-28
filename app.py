"""
Flask 服务端
============

这个项目是「纯 Python」的：没有 app.js、没有 admin.js，
所有页面由 Python 的模板引擎渲染，所有交互靠 HTML 表单提交。

启动：python app.py
"""

import time

from flask import Flask, redirect, render_template, request, url_for

import account
import parking
import plate
import store

app = Flask(__name__)
# session 用来存「待缴费的车牌」。secret_key 是给 session 加密用的，
# 正式项目里应该从环境变量读，不要写死在代码里。
app.secret_key = "parking-demo-secret-key"

PORT = 3000


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
app.jinja_env.filters["fmt_duration"] = fmt_duration


# ----------------------------------------------------------
#  页面
# ----------------------------------------------------------
@app.route("/")
def index():
    """车主端：看车位、入场、出场算钱、缴费。"""
    spots = parking.get_spots()
    free_count = sum(1 for s in spots if s["status"] == "free")
    # 提示信息放在 URL 查询参数里，模板直接用 request.args 取。
    # （不要用 error / status 这种名字，Jinja2 和 Flask 内部有冲突）
    return render_template(
        "index.html",
        spots=spots,
        free_count=free_count,
        msg=request.args.get("msg", ""),
        ok_msg=request.args.get("ok", ""),
        fee=request.args.get("fee", ""),
        minutes=request.args.get("minutes", ""),
    )


@app.route("/admin")
def admin():
    """管理后台：车位总览 + 流水 + 营收。"""
    spots = parking.get_spots()
    logs = store.read_logs()
    free_count = sum(1 for s in spots if s["status"] == "free")
    return render_template(
        "admin.html",
        spots=spots,
        logs=list(reversed(logs)),
        free_count=free_count,
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
    # 分流结果也告诉用户：这辆车按月租还是临停处理的
    channel = result.get("channel")
    if channel == account.MONTHLY:
        tail = "（月租，不计停车费）"
    else:
        tail = "（临停，按小时计费）"
    return redirect(url_for(
        "index", ok_msg=f"入场成功，车位 {spot['id']}（{spot['zone']}）{tail}"))


@app.route("/exit", methods=["POST"])
def exit():
    """出场：先算钱，不放行车位。"""
    raw = (request.form.get("plate") or "").strip()
    if not raw:
        return redirect(url_for("index", msg="车牌号不能为空"))

    result = parking.handle_exit(raw)
    if not result.get("ok"):
        return redirect(url_for("index", msg=result.get("msg", "查询失败")))

    # 把待缴费信息带到下一步。用 session 存比用 URL 参数安全。
    from flask import session
    session["pending_plate"] = plate.normalize(raw)
    return redirect(url_for("index", fee=result["fee"], minutes=result["minutes"]))


@app.route("/pay", methods=["POST"])
def pay():
    """缴费：确认收款，释放车位。"""
    from flask import session
    pending = (session.pop("pending_plate", "") or "").strip()
    if not pending:
        return redirect(url_for("index", msg="没有待缴费的车辆"))

    result = parking.handle_pay(pending)
    if not result.get("ok"):
        return redirect(url_for("index", msg=result.get("msg", "缴费失败")))

    # 月租车费用为 0，但也是「出场了」，分开说清楚
    if result.get("channel") == account.MONTHLY:
        return redirect(url_for("index", ok_msg="月租车出场，车位已释放（本次费用 0 元）"))
    return redirect(url_for("index", ok_msg="缴费成功，车位已释放"))


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
    label = account.kind_label(kind)
    if acc is None:
        label = "临停（未登记）"

    result = parking.handle_entry(raw)
    if not result.get("ok"):
        return redirect(url_for("scan", msg=result.get("msg", "入场失败")))

    spot = result["spot"]
    channel = result.get("channel")
    if channel == account.MONTHLY:
        tip = f"月租车入场，车位 {spot['id']}（{spot['zone']}），本次不计停车费"
    else:
        tip = f"临停车入场，车位 {spot['id']}（{spot['zone']}）"
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
    return render_template(
        "accounts.html",
        accounts=accounts_list,
        stats=account.stats(),
        kind_label=account.kind_label,
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

    result = account.register(raw, kind, holder, note)
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
    print("  账户管理: http://localhost:%d/accounts" % PORT)
    print("  管理后台: http://localhost:%d/admin" % PORT)
    print("=" * 60)
    app.run(host="0.0.0.0", port=PORT, debug=False)
