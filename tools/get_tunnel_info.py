#!/usr/bin/env python3
"""
获取当前有效的公网地址和访问口令。

**为什么需要这个脚本**：公网地址是临时的（Cloudflare 免费隧道 + Actions 保活），
每次部署都变。手动翻聊天记录或仓库页面很容易拿错——拿错了就是「进不去」。

**为什么不用 raw.githubusercontent.com**：
有些网络环境（比如这台机器）DNS 里缺 `raw.githubusercontent.com` 的解析记录，
用它会直接失败。所以这里做了**三个通道自动降级**：

    1. raw.githubusercontent.com   —— 最省事，但 DNS 可能挂
    2. api.github.com             —— 官方 API，最稳（内容是 base64）
    3. github.com/.../raw/...     —— 网页版 raw 通道

哪个通用哪个。

用法：
    python tools/get_tunnel_info.py            # 打印地址和口令
    python tools/get_tunnel_info.py --open     # 顺便用默认浏览器打开
    python tools/get_tunnel_info.py --json     # 输出 JSON（给脚本用）
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "Qyy185/ParkingSystem"
PATH = "tunnel-info.txt"

CHANNELS = [
    # (说明, url, 是否需要 base64 解码)
    ("raw", "https://raw.githubusercontent.com/%s/main/%s" % (REPO, PATH), False),
    ("api", "https://api.github.com/repos/%s/contents/%s" % (REPO, PATH), True),
    ("web", "https://github.com/%s/raw/main/%s" % (REPO, PATH), False),
]

TIMEOUT = 20


def fetch(url):
    """取一个 URL 的内容，失败返回 None。"""
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "parking-get-info",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None
    return raw.decode("utf-8", "replace")


def get_content():
    """依次尝试三个通道，返回 (内容, 用了哪个通道)。"""
    for name, url, is_b64 in CHANNELS:
        body = fetch(url)
        if body is None:
            continue
        if is_b64:
            # API 返回的是 JSON，content 字段是 base64
            try:
                data = json.loads(body)
                return base64.b64decode(data["content"]).decode("utf-8"), name
            except Exception:
                continue
        if body.strip():
            return body, name
    return None, None


def parse(text):
    """从文件内容里解析出地址和口令。

    文件长这样：
        公网地址: https://xxx.trycloudflare.com
        访问口令: ARXGUk6NfoDIWc4Y
        口令来源: 本次运行自动生成的随机口令

    不直接用中文做键名匹配 —— 万一文件编码或格式变了，
    用「找 https:// 开头的那行」更稳。
    """
    url = ""
    pwd = ""
    admin_pwd = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not val:
            continue
        if not url and val.startswith("http"):
            url = val
        elif not pwd and key in ("访问口令", "口令"):
            pwd = val
        elif not admin_pwd and key in ("管理员口令", "管理口令"):
            admin_pwd = val
    return url, pwd, admin_pwd


def main():
    ap = argparse.ArgumentParser(description="获取停车场系统公网地址和口令")
    ap.add_argument("--open", action="store_true", help="顺便用默认浏览器打开")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    text, channel = get_content()
    if not text:
        print("取不到信息文件。三个通道（raw / api / web）都试过了。", file=sys.stderr)
        print("可能是网络问题，稍后重试。", file=sys.stderr)
        return 1

    url, pwd, admin_pwd = parse(text)
    if not url:
        print("取到文件了但没解析出地址，内容如下：", file=sys.stderr)
        print(text, file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"url": url, "password": pwd,
                          "admin_password": admin_pwd, "channel": channel},
                         ensure_ascii=False))
        return 0

    print("公网地址: %s" % url)
    print("访问口令: %s" % pwd)
    if admin_pwd:
        print("管理员口令: %s" % admin_pwd)
    if channel != "raw":
        print("(经由 %s 通道取得)" % channel)

    if args.open:
        try:
            os.startfile(url)
        except Exception:
            # Linux / 无 GUI 环境
            subprocess.call(["xdg-open", url])
    return 0


if __name__ == "__main__":
    sys.exit(main())
