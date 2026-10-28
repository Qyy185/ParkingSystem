#!/usr/bin/env python3
"""
生成 GitHub Contents API 的请求体 —— 用来把公网地址写回仓库的 tunnel-url.txt。

**为什么要单独一个文件**：GitHub Actions 的 YAML 里内联多行 Python 很容易踩缩进的坑
（YAML 块标量 `|` 的内容必须比父级缩进更多，写错了会在 YAML 解析阶段就报错，
而且报错信息很难看出是脚本的问题）。拆成独立文件，日志也好读。

用法（workflow 里）：
    export EXISTING_SHA="$EXISTING"     # 文件已存在时的 sha，不存在传空
    export TARGET_URL="$TUNNEL_URL"     # 要写入的公网地址
    PAYLOAD=$(python3 tools/sync_tunnel_url.py)

输出：JSON 请求体，直接喂给 curl 的 -d 参数。
"""

import base64
import json
import os
import sys

# 相对仓库根目录的路径
TARGET_PATH = "tunnel-url.txt"


def main():
    url = os.environ.get("TARGET_URL", "").strip()
    if not url:
        print("TARGET_URL 为空，无法生成请求体", file=sys.stderr)
        return 1

    sha = os.environ.get("EXISTING_SHA", "").strip()
    body = {
        "message": "chore: 更新公网访问地址",
        "content": base64.b64encode(url.encode("utf-8")).decode("ascii"),
    }
    # 文件已存在时必须带 sha，否则 API 返回 422
    if sha:
        body["sha"] = sha

    print(json.dumps(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
