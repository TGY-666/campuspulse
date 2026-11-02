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

"%PY%" -c "import flask, flask_cors, pandas, openpyxl, jieba, requests" >nul 2>&1
if errorlevel 1 goto NO_DEPS

goto RUN

:NO_VENV
echo [提示] 未找到虚拟环境 .venv，正在自动创建（首次运行需几分钟）...
echo.
set "BASE_PY="
where python >nul 2>&1 && set "BASE_PY=python"
if not defined BASE_PY where py >nul 2>&1 && set "BASE_PY=py"
if not defined BASE_PY goto NO_PYTHON

%BASE_PY% -m venv .venv
if errorlevel 1 goto VENV_FAIL
if not exist "%PY%" goto VENV_FAIL
echo [完成] 虚拟环境已创建。
echo.
goto NO_DEPS

:NO_PYTHON
echo [错误] 这台电脑没有找到 Python，请先安装 Python 3.10 或更高版本。
echo        下载地址：https://www.python.org/downloads/
echo        安装时请勾选 "Add Python to PATH"。
goto END_FAIL

:VENV_FAIL
echo [错误] 创建虚拟环境失败，请确认 Python 已正确安装。
goto END_FAIL

:NO_DEPS
echo [提示] 正在安装项目依赖，请保持联网，大约 1-3 分钟...
echo.
"%PY%" -m pip install --upgrade pip -q
"%PY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 goto PIP_FAIL
echo.
echo [完成] 依赖安装完毕。
echo.
goto RUN

:PIP_FAIL
echo.
echo [错误] 依赖安装失败，请检查网络后重新双击本文件。
echo        也可手动执行：.venv\Scripts\python.exe -m pip install -r requirements.txt
goto END_FAIL

:RUN
set "AUTO_OPEN_BROWSER=0"
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
