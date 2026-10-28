#!/usr/bin/env python3
"""
设置 GitHub Actions 仓库级 Secret —— 不用进网页点按钮。

**为什么需要这个脚本**：
GitHub 网页上的入口很深（Settings → Security → Secrets and variables → Actions），
很多人找不到，也懒得点。Secret 本身就是个 API 能写的东西：

    PUT /repos/{owner}/{repo}/actions/secrets/{secret_name}

**但有个坑**：GitHub 不接受明文。必须用仓库的「公钥」把值做一次
**libsodium sealed box** 加密，再把密文 base64 传上去。GitHub 用自己的私钥解开。
公钥从 `GET /repos/{owner}/{repo}/actions/secrets/public-key` 拿。

    pip install pynacl

**用法**：

    # 从环境变量读 token（GH_TOKEN / GITHUB_TOKEN 二选一）
    export GH_TOKEN=ghp_xxx
    python tools/set_github_secret.py ACCESS_PWD demo2026
    python tools/set_github_secret.py SECRET_KEY a8f3k2m9x7

    # 或者指定仓库
    python tools/set_github_secret.py --repo Qyy185/ParkingSystem ACCESS_PWD demo2026

**注意**：Secret 只能读、不能读回值。设完之后 GitHub 会在日志里把值显示成 ***，
所以要想清楚再设，设完自己记在哪儿。
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"


def _request(url, method="GET", token=None, body=None):
    """发一个 GitHub API 请求，返回 (状态码, 解析后的 JSON 或原始文本)。"""
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "parking-system-secret-setter",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    except urllib.error.URLError as e:
        return 0, {"error": "网络错误: %s" % e.reason}

    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def seal_value(plaintext, public_key_b64):
    """用仓库公钥做 libsodium sealed box 加密，返回 base64 密文。"""
    try:
        from nacl import public, encoding
    except ImportError:
        print("缺少依赖 pynacl，请先执行：pip install pynacl", file=sys.stderr)
        raise SystemExit(2)

    key = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder)
    box = public.SealedBox(key)
    sealed = box.seal(plaintext.encode("utf-8"))
    return base64.b64encode(sealed).decode("ascii")


def set_secret(repo, name, value, token):
    """设置一个仓库级 Secret。返回 True 表示成功。"""
    # 1. 拿仓库的加密公钥
    status, info = _request(
        "%s/repos/%s/actions/secrets/public-key" % (API, repo), token=token
    )
    if status != 200 or not isinstance(info, dict) or "key" not in info:
        print("❌ 拿公钥失败 HTTP %s" % status, file=sys.stderr)
        print("   %s" % info, file=sys.stderr)
        if status == 403:
            print("   → token 没有 repo 权限，或者你不是这个仓库的主人", file=sys.stderr)
        elif status == 404:
            print("   → 仓库名不对？确认一下 owner/repo 拼写", file=sys.stderr)
        return False

    key_id = info["key_id"]
    public_key = info["key"]

    # 2. 加密
    try:
        encrypted = seal_value(value, public_key)
    except SystemExit:
        raise
    except Exception as e:
        print("❌ 加密失败: %s" % e, file=sys.stderr)
        return False

    # 3. 写进去
    status, result = _request(
        "%s/repos/%s/actions/secrets/%s" % (API, repo, name),
        method="PUT",
        token=token,
        body={
            "encrypted_value": encrypted,
            "key_id": key_id,
        },
    )

    if status in (201, 204):
        print("✅ 已设置 Secret：%s（%s）" % (name, repo))
        return True

    print("❌ 设置失败 HTTP %s" % status, file=sys.stderr)
    print("   %s" % result, file=sys.stderr)
    if status == 422:
        print("   → 值不合法，或者 key_id 和公钥不匹配（换一次公钥再试）", file=sys.stderr)
    return False


def main():
    ap = argparse.ArgumentParser(description="设置 GitHub Actions 仓库级 Secret")
    ap.add_argument("name", help="Secret 名称，比如 ACCESS_PWD")
    ap.add_argument("value", help="Secret 的值")
    ap.add_argument("--repo", default="Qyy185/ParkingSystem", help="owner/repo")
    ap.add_argument("--token", default=None, help="GitHub token（默认读环境变量 GH_TOKEN）")
    args = ap.parse_args()

    token = args.token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "缺少 token。用环境变量 GH_TOKEN=xxx 或者 --token 参数传。",
            file=sys.stderr,
        )
        return 2

    if set_secret(args.repo, args.name, args.value, token):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
