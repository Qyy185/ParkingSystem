# MEMORY.md — 项目长期笔记

## 环境（这台机器）

- OS：Windows，用户名 `36151`，桌面 `C:/Users/36151/Desktop`
- 控制台默认代码页 **936（GBK）**
- Node：系统版 `C:/Program Files\nodejs\node.exe`
- Python：WorkBuddy 自带 3.13.12（`C:/Users/36151/.workbuddy/binaries/python/versions/3.13.12/python.exe`），
  也有 conda 的 `E:/conda/python.exe`

## ★ DNS 缺记录（踩过，别再踩）

这台机器的 DNS 里**缺 `raw.githubusercontent.com` 的解析记录**。

| 域名 | 能否解析 |
|---|---|
| github.com / api.github.com | ✓ |
| objects.githubusercontent.com / codeload.github.com | ✓ |
| **raw.githubusercontent.com** | **✗** |

后果：任何用 raw.githubusercontent.com 取文件的东西都会失败
（curl 返 000、Invoke-WebRequest 报「未能解析此远程名称」）。
**要取仓库文件内容，改用：**

```
https://api.github.com/repos/{owner}/{repo}/contents/{path}
```
返回 JSON，`content` 字段是 base64，解一下即可。

**写脚本要取仓库文件时，务必做多通道降级**，别只押 raw。
