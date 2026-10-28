"""
自检脚本：验证服务器能起来、页面能出、表单能提交。

用法（在项目目录下）：
    .venv\\Scripts\\python.exe tools\\selftest.py
"""

import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 3000
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

results = []


def check(name, ok, extra=""):
    results.append({"name": name, "ok": ok, "extra": extra})


def get(path):
    """GET 一个页面，返回 (状态码, 文本)。"""
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def post(path, data):
    """POST 表单。返回 (最终状态码, 最终 URL, 文本)。

    表单提交后会 302 跳转到带提示参数的地址，
    所以这里要看最终 URL 里有没有 error= / ok= 参数。
    """
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (PORT, path),
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.geturl(), resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.geturl(), e.read().decode("utf-8")


def main():
    print("=== 启动服务器 ===")
    proc = subprocess.Popen(
        [PY, os.path.join(ROOT, "app.py")],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    time.sleep(2.5)

    try:
        # 1. 车主端页面
        status, text = get("/")
        check("GET / 200", status == 200, "status=%d" % status)
        check("车主端含标题", "地下停车场" in text)
        cells = re.findall(r'class="cell ', text)
        check("渲染出 16 个车位", len(cells) == 16, "实际 %d" % len(cells))
        check("含入场表单", 'action="/entry"' in text)
        check("含出场表单", 'action="/exit"' in text)

        # 2. 管理后台
        status, text = get("/admin")
        check("GET /admin 200", status == 200, "status=%d" % status)
        check("后台含流水表", "进出场流水" in text)

        # 3. 静态资源
        status, _ = get("/static/style.css")
        check("GET /static/style.css 200", status == 200, "status=%d" % status)

        # 4. 业务逻辑
        #    表单提交会 302 跳到 /?msg=... 或 /?ok_msg=...
        status, url, text = post("/entry", {"plate": "黑A12345"})
        check("POST /entry 有响应",
              status == 200 and "msg=" in url, url)

        # 5. 空车牌被拦
        status, url, text = post("/entry", {"plate": ""})
        check("POST /entry 空车牌被拦下",
              status == 200 and "%E4%B8%8D%E8%83%BD%E4%B8%BA%E7%A9%BA" in url
              or "不能为空" in url, url)

        # 6. 提示信息真的渲染到页面上了
        status, text = get("/?msg=%E6%B5%8B%E8%AF%95%E6%8F%90%E7%A4%BA")
        check("提示信息渲染到页面", "测试提示" in text)

        # 7. 404
        status, _ = get("/no-such-page")
        check("不存在的页面返回 404", status == 404, "status=%d" % status)

    except Exception as e:
        check("请求过程无异常", False, str(e))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print()
    print("=== 自检结果 ===")
    passed = 0
    for r in results:
        print("[%s] %s%s" % ("PASS" if r["ok"] else "FAIL", r["name"],
                              ("  (" + r["extra"] + ")") if r["extra"] else ""))
        if r["ok"]:
            passed += 1
    print("---")
    print("%d / %d 通过" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
