@echo off
chcp 65001 >nul
setlocal

REM Double-click friendly launcher. Keeps the window open if startup fails.
call "%~dp0run.bat"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [start] startup failed with exit code %EXIT_CODE%.
    echo [start] check scripts\data\service.err.log if this was started as a service.
    echo.
    pause
)

exit /b %EXIT_CODE%
