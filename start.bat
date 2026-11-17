@echo off
setlocal
cd /d "%~dp0"
title CampusPulse 高校舆情智能洞察平台
cls

echo ============================================================
echo    CampusPulse 高校舆情智能洞察平台 - 启动器
echo ============================================================
echo.

set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" goto NO_VENV

rem Python 3.15 preview ships a broken pip; reject it and rebuild.
"%PY%" -c "import sys; raise SystemExit(0 if sys.version_info < (3,15) else 1)" >nul 2>&1
if errorlevel 1 goto BAD_VENV

"%PY%" -c "import flask, flask_cors, pandas, openpyxl, jieba, requests, rapidocr_onnxruntime, PIL, mcp, playwright" >nul 2>&1
if errorlevel 1 goto NO_DEPS

goto RUN

:BAD_VENV
echo [提示] 检测到 .venv 使用了不兼容的 Python 3.15 预览版。
echo        将备份旧环境并重建，旧环境会保留为 .venv_broken。
if exist ".venv_broken" goto BAD_VENV_EXISTS
move .venv .venv_broken >nul 2>&1
if exist ".venv" goto VENV_FAIL
goto NO_VENV

:BAD_VENV_EXISTS
echo [错误] 已存在 .venv_broken，请先手动删除或改名后重试。
goto END_FAIL

:NO_VENV
echo [提示] 未找到可用的虚拟环境 .venv，正在自动创建（首次运行需几分钟）...
echo.
set "BASE_PY="

rem Prefer stable Python 3.11, then 3.12 / 3.10 / 3.13.
if not defined BASE_PY (
  py -3.11 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "BASE_PY=py -3.11"
)
if not defined BASE_PY (
  py -3.12 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "BASE_PY=py -3.12"
)
if not defined BASE_PY (
  py -3.10 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "BASE_PY=py -3.10"
)
if not defined BASE_PY (
  py -3.13 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "BASE_PY=py -3.13"
)
if not defined BASE_PY (
  python -c "import sys; raise SystemExit(0 if (3,10)<=sys.version_info<(3,15) else 1)" >nul 2>&1
  if not errorlevel 1 set "BASE_PY=python"
)
if not defined BASE_PY goto NO_PYTHON

%BASE_PY% -m venv .venv
if errorlevel 1 goto VENV_FAIL
if not exist "%PY%" goto VENV_FAIL
echo [完成] 虚拟环境已创建。
echo.
goto NO_DEPS

:NO_PYTHON
echo [错误] 未找到可用的 Python 3.10-3.14。
echo        请安装 Python 3.11（推荐）后重试。
echo        不要使用 Python 3.15 预览版，其自带 pip 可能不可用。
echo        下载地址：https://www.python.org/downloads/
echo        安装时请勾选 "Add Python to PATH"。
goto END_FAIL

:VENV_FAIL
echo [错误] 创建虚拟环境失败，请确认 Python 已正确安装。
goto END_FAIL

:NO_DEPS
echo [提示] 正在安装全部依赖（含 OCR / MCP / Playwright），请保持联网...
echo.
"%PY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 goto PIP_FAIL
echo.
echo [完成] Python 依赖安装完毕。
echo.
echo [提示] 正在安装 Chromium 浏览器内核（首次约 200MB，已安装会跳过）...
"%PY%" -m playwright install chromium
if errorlevel 1 goto CHROMIUM_FAIL
echo [完成] Chromium 浏览器内核就绪。
echo.
goto RUN

:CHROMIUM_FAIL
echo [提示] Chromium 安装失败：实时采集暂不可用，其他功能不受影响。
echo        可手动重试：.venv\Scripts\python.exe -m playwright install chromium
echo.
goto RUN

:PIP_FAIL
echo.
echo [错误] 依赖安装失败，请检查网络后重新双击本文件。
echo        也可手动执行：.venv\Scripts\python.exe -m pip install -r requirements.txt
echo        若仍失败，请确认使用的是 Python 3.11，而不是 3.15 预览版。
goto END_FAIL

:RUN
rem Demo mode: login + editable tickets.
set "CP_AUTH=1"
set "CP_TICKETS=1"
if not defined AUTO_OPEN_BROWSER set "AUTO_OPEN_BROWSER=1"
echo ============================================================
echo   已开启：登录 + 处置工单（6 个一级入口 / 12 个页面）
echo   账号：
echo     管理员   见 data\initial_admin.txt
echo     演示角色 见 data\demo_accounts.txt（分析员 / 处置员 / 只读）
echo ============================================================
echo.
echo 正在启动服务，浏览器会自动打开，请稍候...
echo 停止服务：关闭本窗口，或按 Ctrl+C。
echo.
"%PY%" run.py
echo.
echo 服务已停止。
goto END_OK

:END_FAIL
echo.
pause
exit /b 1

:END_OK
pause
exit /b 0
