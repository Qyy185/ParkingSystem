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


def read_all():
    """读所有车位。文件不存在 / 损坏 / 不是列表，一律返回空列表，绝不让程序挂掉。"""
    try:
        with open(FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def write_all(data):
    """写所有车位。data 文件夹不存在会自动建。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_logs():
    """读所有流水。"""
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def add_log(entry):
    """记一笔流水，供管理后台统计营收用。"""
    logs = read_logs()
    record = {"time": now_ms()}
    record.update(entry)
    logs.append(record)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


def now_ms():
    """当前时间的毫秒时间戳。"""
    return int(time.time() * 1000)


def today_revenue():
    """今天收了多少钱。管理后台用。"""
    today = time.strftime("%Y-%m-%d")
    total = 0
    for log in read_logs():
        if log.get("type") != "pay":
            continue
        stamp = time.strftime("%Y-%m-%d", time.localtime(log.get("time", 0) / 1000))
        if stamp == today:
            total += log.get("fee", 0)
    return total
