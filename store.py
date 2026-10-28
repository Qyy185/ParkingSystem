"""
数据读写层
=========
纯读写的工具层，没有业务逻辑。你可以直接用，不用改。
"""

import json
import os
import time

# 项目根目录 = 本文件所在的这一层（store.py 就在根目录下）
# __file__ 是本文件的绝对路径，dirname 去掉文件名 → 得到本文件所在目录
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
FILE = os.path.join(DATA_DIR, "parking.json")
LOG_FILE = os.path.join(DATA_DIR, "logs.json")
ACCOUNT_FILE = os.path.join(DATA_DIR, "accounts.json")
RECHARGE_FILE = os.path.join(DATA_DIR, "recharges.json")
NOTIFICATION_FILE = os.path.join(DATA_DIR, "notifications.json")


def _read_list(path):
    """读一个 JSON 数组文件。文件不存在 / 损坏 / 不是列表，一律返回空列表。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_list(path, data):
    """写一个 JSON 数组文件。data 文件夹不存在会自动建。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_all():
    """读所有车位。"""
    return _read_list(FILE)


def write_all(data):
    """写所有车位。"""
    _write_list(FILE, data)


def read_logs():
    """读所有流水。"""
    return _read_list(LOG_FILE)


def add_log(entry):
    """记一笔流水，供管理后台统计营收用。"""
    logs = read_logs()
    record = {"time": now_ms()}
    record.update(entry)
    logs.append(record)
    _write_list(LOG_FILE, logs)


# ---------------------------------------------------------
# 账户表（v0.2.1）
# ---------------------------------------------------------
# 账户 = 车牌 + 类型标记。类型只有两种：
#   monthly    月租车主
#   temporary  临停车主
# 账户里【没有金额、没有余额、没有付款记录】——
# 它只回答一个问题：这辆车按哪套规则处理。

def read_accounts():
    """读所有账户。"""
    return _read_list(ACCOUNT_FILE)


def write_accounts(data):
    """写所有账户。"""
    _write_list(ACCOUNT_FILE, data)


# ---------------------------------------------------------
# 充值流水表（v0.2.2）
# ---------------------------------------------------------
# 充值和停车费付款是两条线：这里只记「买套餐」，
# 停车费的 pay 流水仍然只写在 logs.json 里。

def read_recharges():
    """读所有充值记录。"""
    return _read_list(RECHARGE_FILE)


def write_recharges(data):
    """写所有充值记录。"""
    _write_list(RECHARGE_FILE, data)


def add_recharge(entry):
    """记一笔充值。"""
    records = read_recharges()
    record = {"time": now_ms()}
    record.update(entry)
    records.append(record)
    _write_list(RECHARGE_FILE, records)


# ---------------------------------------------------------
# 通知记录表（v0.3.1）
# ---------------------------------------------------------
# 催缴短信/微信的发送记录。每条记清楚「发给谁、发的是什么、什么时候发的」，
# 这样催缴这个动作是可审计的 —— 出了纠纷能查。

def read_notifications():
    """读所有通知记录。"""
    return _read_list(NOTIFICATION_FILE)


def write_notifications(data):
    """写所有通知记录。"""
    _write_list(NOTIFICATION_FILE, data)


def add_notification(entry):
    """记一条通知发送记录。"""
    records = read_notifications()
    record = {"time": now_ms()}
    record.update(entry)
    records.append(record)
    _write_list(NOTIFICATION_FILE, records)


def now_ms():
    """当前时间的毫秒时间戳。"""
    return int(time.time() * 1000)


def today_revenue():
    """今天收了多少钱。管理后台用。

    只统计 `type == "pay"` 的流水 —— 这是【临停】付款。
    月租账户的入场不产生任何 pay 记录，所以不会混进来。
    """
    today = time.strftime("%Y-%m-%d")
    total = 0
    for log in read_logs():
        if log.get("type") != "pay":
            continue
        stamp = time.strftime("%Y-%m-%d", time.localtime(log.get("time", 0) / 1000))
        if stamp == today:
            total += log.get("fee", 0)
    return total

