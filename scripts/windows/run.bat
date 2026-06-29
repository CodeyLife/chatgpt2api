@echo off
chcp 65001 >nul
setlocal

REM ============================================
REM ChatGPT2API runner (called by nssm service, or run manually)
REM uv lookup order: env UV_PATH > bundled bin\uv.exe > system PATH
REM ============================================

REM Switch to project root (two levels up from this script)
pushd "%~dp0..\.."

REM Locate uv.exe
if defined UV_PATH (
    set "UV_CMD=%UV_PATH%"
) else if exist "%~dp0bin\uv.exe" (
    set "UV_CMD=%~dp0bin\uv.exe"
) else (
    set "UV_CMD=uv"
)

REM Port (default 9188, overridable via env var)
if not defined CHATGPT2API_PORT set "CHATGPT2API_PORT=9188"

REM This project imports GitPython even when STORAGE_BACKEND=json. On servers
REM without git.exe, keep GitPython quiet so JSON/SQLite mode can still start.
if not defined GIT_PYTHON_REFRESH set "GIT_PYTHON_REFRESH=quiet"

REM Optional dependency sync. Enable with CHATGPT2API_SYNC=1 after code updates.
if /I "%CHATGPT2API_SYNC%"=="1" (
    echo [run] syncing dependencies...
    "%UV_CMD%" sync
    if errorlevel 1 (
        echo [run] uv sync failed
        popd
        exit /b 1
    )
)

REM Refuse to start a second instance on the same port.
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -State Listen -LocalPort %CHATGPT2API_PORT% -ErrorAction SilentlyContinue) { exit 1 } else { exit 0 }"
if errorlevel 1 (
    echo [run] port %CHATGPT2API_PORT% is already in use.
    echo [run] open http://127.0.0.1:%CHATGPT2API_PORT%/ or stop the existing process first.
    popd
    exit /b 1
)

echo [run] uv = %UV_CMD%
echo [run] port = %CHATGPT2API_PORT%
echo [run] url = http://127.0.0.1:%CHATGPT2API_PORT%/
"%UV_CMD%" run uvicorn main:app --host 0.0.0.0 --port %CHATGPT2API_PORT% --access-log

set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
