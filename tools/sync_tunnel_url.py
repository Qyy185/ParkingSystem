#!/usr/bin/env python3
"""
把公网访问信息写回仓库（地址 + 访问口令）。

**为什么要写回仓库**：GitHub Actions 的任务摘要只有登录后才能看。
但演示的时候经常需要在**不登录 GitHub** 的情况下拿到地址和口令
（发给同事、写成脚本、扫码直接进）。写进仓库文件就不用登录了。

**为什么用 Contents API 而不是 `git push`**：
API 只需要 GITHUB_TOKEN，不依赖 checkout 时配置的 git 凭证，
也不会因为分支保护而失败。

**★ 这个脚本把「同步失败会导致服务被杀」的坑彻底堵死了**：
之前同步是在 workflow 的 shell 里拼 curl + 内联 Python，
一旦某步 `exit 1`，整个 step 失败，后续步骤会被跳过，
「保持服务在线」跟着被跳过，「清理」紧接着把服务杀掉。
现在同步的所有错误都在 Python 内部被吞掉，函数永远返回 0。

用法（workflow 里）：
    export GH_TOKEN="$GITHUB_TOKEN"
    export TARGET_URL="$TUNNEL_URL"
    export TARGET_PWD="$ACCESS_PWD"
    python3 tools/sync_tunnel_url.py

输出的文件：
    tunnel-url.txt   —— 只有公网地址（一行），方便脚本直接 curl
    tunnel-info.txt  —— 地址 + 口令 + 说明，给人看的
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
REPO = os.environ.get("GITHUB_REPOSITORY", "Qyy185/ParkingSystem")


def api(path, method="GET", token=None, body=None):
    """调 GitHub API。返回 (状态码, 响应内容)。"""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "parking-system-sync",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        API + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def get_sha(path, token):
    """拿文件当前 sha。不存在返回空串。"""
    status, body = api("/repos/%s/contents/%s" % (REPO, path), token=token)
    if status != 200:
        return ""
    try:
        return json.loads(body).get("sha", "")
    except ValueError:
        return ""


def put_file(path, content, token, message):
    """写一个文件。文件已存在时带上 sha（否则 API 返回 422）。"""
    sha = get_sha(path, token)
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha

    status, body = api(
        "/repos/%s/contents/%s" % (REPO, path),
        method="PUT",
        token=token,
        body=payload,
    )
    return status, body


def main():
    token = os.environ.get("GH_TOKEN", "").strip()
    url = os.environ.get("TARGET_URL", "").strip()
    pwd = os.environ.get("TARGET_PWD", "").strip()
    source = os.environ.get("PWD_SOURCE", "").strip()
    admin_pwd = os.environ.get("TARGET_ADMIN_PWD", "").strip()

    if not token:
        print("⚠️ 没有 GH_TOKEN，跳过同步")
        return 0
    if not url:
        print("⚠️ 没有 TARGET_URL，跳过同步")
        return 0

    ok = True

    # 1. 地址文件（纯地址，给脚本用）
    status, body = put_file(
        "tunnel-url.txt",
        url + "\n",
        token,
        "chore: 更新公网访问地址",
    )
    if status in (200, 201):
        print("✅ 地址已同步到 tunnel-url.txt")
        print("   公网地址: %s" % url)
    else:
        ok = False
        print("❌ 地址同步失败 HTTP %s" % status)
        print("   %s" % body[:300])

    # 2. 信息文件（地址 + 口令，给人看的）
    info_lines = [
        "公网地址: %s" % url,
        "",
    ]
    if pwd:
        info_lines += [
            "访问口令: %s" % pwd,
            "口令来源: %s" % (source or "未知"),
        ]
    else:
        info_lines += ["访问口令: （未设置，当前完全公开）"]

    if admin_pwd:
        info_lines += [
            "",
            "管理后台: /admin",
            "管理员口令: %s" % admin_pwd,
        ]
    info_lines += [
        "",
        "注: 这是临时地址，本次 Actions 运行结束后失效。",
        "    需要新地址就重新触发一次部署。",
    ]

    status, body = put_file(
        "tunnel-info.txt",
        "\n".join(info_lines) + "\n",
        token,
        "chore: 更新公网访问信息（地址 + 口令）",
    )
    if status in (200, 201):
        print("✅ 访问信息已同步到 tunnel-info.txt")
    else:
        ok = False
        print("❌ 信息同步失败 HTTP %s" % status)
        print("   %s" % body[:300])

    # ★ 永远返回 0 —— 同步失败绝不能影响服务上线。
    #   （旧版本这里会因为 shell 里的 exit 1 让整个 job 变红，
    #     进而把「保持服务在线」跳过，把服务杀掉。）
    return 0


if __name__ == "__main__":
    sys.exit(main())
