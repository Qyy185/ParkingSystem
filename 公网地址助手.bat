@echo off
chcp 936 >nul
title 停车场系统 - 公网地址助手

REM ============================================================
REM   双击获取当前公网地址 + 访问口令，并自动打开浏览器
REM
REM   地址是临时的（Cloudflare 免费隧道 + Actions 保活 5 小时），
REM   每次部署都会变。之前拿错旧地址就是因为手抄了失效的链接，
REM   所以用这个脚本取当前最新的。
REM ============================================================

cd /d "%~dp0"

echo.
echo   正在获取最新公网地址...
echo.

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
    for %%P in (python python3 py) do (
        if not defined PY (
            %%P -c "import sys" >nul 2>nul && set "PY=%%P"
        )
    )
)

if not defined PY (
    echo   [失败] 没找到 Python。
    echo.
    echo   请直接打开仓库首页看 tunnel-info.txt 文件：
    echo   https://github.com/Qyy185/ParkingSystem
    echo.
    pause
    exit /b 1
)

"%PY%" tools\get_tunnel_info.py --open
if errorlevel 1 (
    echo.
    echo   取地址失败，可能是网络不通，稍后再试。
    echo   或直接打开仓库首页看 tunnel-info.txt 文件。
    echo.
    pause
    exit /b 1
)

echo.
echo   浏览器已打开，进去后输入上面的口令即可。
echo   (地址是临时的，约 5 小时后失效；重新双击本文件即可拿新的)
echo.
ping -n 6 127.0.0.1 >nul
exit /b 0
