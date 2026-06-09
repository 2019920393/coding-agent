@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "WORKBENCH=%ROOT%\workbench-app"
set "VENV=%ROOT%\venv"
set "VENV_PYTHON=%VENV%\Scripts\python.exe"

echo ========================================
echo   Codo Workbench 一键启动
echo ========================================
echo.

cd /d "%ROOT%"

if not exist "%WORKBENCH%\package.json" (
    echo [错误] 未找到 workbench-app\package.json
    echo 当前目录: %ROOT%
    pause
    exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Node.js，请先安装 Node.js。
    pause
    exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 npm，请确认 Node.js 安装完整。
    pause
    exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+。
    pause
    exit /b 1
)

echo [1/4] 检查 Node 依赖...
set "NEED_NPM_INSTALL=0"
if not exist "%WORKBENCH%\node_modules\.bin\vite.cmd" set "NEED_NPM_INSTALL=1"
if not exist "%WORKBENCH%\node_modules\.bin\tsup.cmd" set "NEED_NPM_INSTALL=1"
if not exist "%WORKBENCH%\node_modules\.bin\wait-on.cmd" set "NEED_NPM_INSTALL=1"
if not exist "%WORKBENCH%\node_modules\.bin\concurrently.cmd" set "NEED_NPM_INSTALL=1"
if not exist "%WORKBENCH%\node_modules\electron\dist\electron.exe" set "NEED_NPM_INSTALL=1"

if "%NEED_NPM_INSTALL%"=="1" (
    echo   依赖缺失或不完整，执行 npm install...
    pushd "%WORKBENCH%"
    call npm install
    if errorlevel 1 (
        popd
        echo [错误] npm install 失败。
        pause
        exit /b 1
    )
    popd
) else (
    echo   Node 依赖可用。
)

echo.
echo [2/4] 检查 Python 环境...
if not exist "%VENV_PYTHON%" (
    echo   未找到 venv，正在创建虚拟环境...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo [错误] Python 虚拟环境创建失败。
        pause
        exit /b 1
    )
)

"%VENV_PYTHON%" -c "import anthropic, dotenv, lsprotocol, mcp, pydantic, pygls, yaml" >nul 2>nul
if errorlevel 1 (
    echo   Python 依赖缺失，正在安装项目依赖...
    call "%VENV_PYTHON%" -m pip install --upgrade pip
    if errorlevel 1 (
        echo [错误] pip 升级失败。
        pause
        exit /b 1
    )

    call "%VENV_PYTHON%" -m pip install -e "%ROOT%"
    if errorlevel 1 (
        echo [错误] Python 项目依赖安装失败。
        pause
        exit /b 1
    )
) else (
    echo   Python 依赖可用。
)

echo.
echo [3/4] 检查 Electron 构建产物...
if not exist "%WORKBENCH%\dist-electron\main.cjs" (
    echo   main.cjs 不存在，正在构建 Electron 主进程...
    pushd "%WORKBENCH%"
    call npx tsup electron/main.ts electron/preload.ts --format cjs --platform node --external electron --out-dir dist-electron --clean
    if errorlevel 1 (
        popd
        echo [错误] Electron 主进程构建失败。
        pause
        exit /b 1
    )
    popd
)

if not exist "%WORKBENCH%\dist-electron\preload.cjs" (
    echo   preload.cjs 不存在，正在构建 Electron 产物...
    pushd "%WORKBENCH%"
    call npx tsup electron/main.ts electron/preload.ts --format cjs --platform node --external electron --out-dir dist-electron --clean
    if errorlevel 1 (
        popd
        echo [错误] Electron preload 构建失败。
        pause
        exit /b 1
    )
    popd
)

echo   Electron 构建产物可用。

echo.
echo [4/4] 启动 Codo Workbench...
echo   前端地址: http://127.0.0.1:5173
echo   Python: %VENV_PYTHON%
echo.
echo 关闭窗口或按 Ctrl+C 可以停止本次启动进程。
echo.

set "CODO_PYTHON=%VENV_PYTHON%"
set ELECTRON_RUN_AS_NODE=

pushd "%WORKBENCH%"
call npm run desktop
set "EXIT_CODE=%ERRORLEVEL%"
popd

echo.
echo ========================================
echo   Codo Workbench 已退出，退出码: %EXIT_CODE%
echo ========================================
pause
exit /b %EXIT_CODE%
