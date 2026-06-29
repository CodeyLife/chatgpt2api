@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ============================================
rem chatgpt2api build and push helper
rem
rem Usage:
rem   build-push.bat <version>
rem   build-push.bat <version> --no-git
rem   build-push.bat <version> --dry-run
rem
rem Optional environment variables:
rem   DOCKER_USERNAME / DOCKER_PASSWORD  Log in to Docker Hub before pushing.
rem   ACR_USERNAME / ACR_PASSWORD        Legacy alias for DOCKER_* (still accepted).
rem   BUILD_PUSH_MODE             buildx or docker. Default: docker.
rem   SKIP_GIT                    1 skips git commit/tag/push.
rem ============================================

set "RELEASE_VERSION=%~1"
set "OPTION=%~2"

if "%RELEASE_VERSION%"=="" (
    echo [ERROR] Missing version argument.
    echo Usage: %~nx0 ^<version^> [--no-git^|--dry-run]
    exit /b 1
)

if "%OPTION%"=="--no-git" set "SKIP_GIT=1"
if "%OPTION%"=="--dry-run" set "DRY_RUN=1"
if not "%OPTION%"=="" if not "%OPTION%"=="--no-git" if not "%OPTION%"=="--dry-run" (
    echo [ERROR] Unknown option: %OPTION%
    echo Usage: %~nx0 ^<version^> [--no-git^|--dry-run]
    exit /b 1
)

set "DOCKER_NAMESPACE=yjj5918"
set "IMAGE_NAME=chatgpt2api"
set "IMAGE_BASE=%DOCKER_NAMESPACE%/%IMAGE_NAME%"
set "PLATFORM=linux/amd64"
set "BUILD_PUSH_MODE=%BUILD_PUSH_MODE%"
if "%BUILD_PUSH_MODE%"=="" set "BUILD_PUSH_MODE=docker"

cd /d "%~dp0"
if errorlevel 1 (
    echo [ERROR] Failed to change to script directory.
    exit /b 1
)

echo.
echo ============================================
echo chatgpt2api build and push v%RELEASE_VERSION%
echo Image: %IMAGE_BASE%
echo Platform: %PLATFORM%
echo Mode: %BUILD_PUSH_MODE%
echo ============================================
echo.

where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] docker was not found in PATH.
    exit /b 1
)

where git >nul 2>&1
if errorlevel 1 (
    echo [WARN] git was not found in PATH. Git steps will be skipped.
    set "SKIP_GIT=1"
)

if not defined DOCKER_USERNAME (
    if defined ACR_USERNAME set "DOCKER_USERNAME=%ACR_USERNAME%"
)
if not defined DOCKER_PASSWORD (
    if defined ACR_PASSWORD set "DOCKER_PASSWORD=%ACR_PASSWORD%"
)
if not defined DOCKER_USERNAME (
    echo [1/7] Skipping docker login. Assuming docker is already logged in.
) else if not defined DOCKER_PASSWORD (
    echo [1/7] Skipping docker login. DOCKER_PASSWORD is not set.
) else (
    echo [1/7] Logging in to Docker Hub...
    if defined DRY_RUN (
        echo DRY-RUN: docker login -u %DOCKER_USERNAME% --password-stdin
    ) else (
        echo !DOCKER_PASSWORD!| docker login -u !DOCKER_USERNAME! --password-stdin
        if errorlevel 1 (
            echo [ERROR] docker login failed.
            exit /b 1
        )
    )
)

echo.
echo [2/7] Updating VERSION -^> %RELEASE_VERSION%
if defined DRY_RUN (
    echo DRY-RUN: write %RELEASE_VERSION% to VERSION
) else (
    > VERSION echo %RELEASE_VERSION%
    if errorlevel 1 (
        echo [ERROR] Failed to write VERSION.
        exit /b 1
    )
)

echo.
echo [3/7] Building image...
if /I "%BUILD_PUSH_MODE%"=="buildx" (
    if defined DRY_RUN (
        echo DRY-RUN: docker buildx build --platform %PLATFORM% -t %IMAGE_BASE%:%RELEASE_VERSION% -t %IMAGE_BASE%:latest --push .
    ) else (
        docker buildx build ^
            --platform %PLATFORM% ^
            -t %IMAGE_BASE%:%RELEASE_VERSION% ^
            -t %IMAGE_BASE%:latest ^
            --push ^
            .
        if errorlevel 1 (
            echo [ERROR] buildx build/push failed.
            exit /b 1
        )
    )
) else if /I "%BUILD_PUSH_MODE%"=="docker" (
    if defined DRY_RUN (
        echo DRY-RUN: docker build --platform %PLATFORM% -t %IMAGE_BASE%:%RELEASE_VERSION% -t %IMAGE_BASE%:latest .
    ) else (
        docker build ^
            --platform %PLATFORM% ^
            -t %IMAGE_BASE%:%RELEASE_VERSION% ^
            -t %IMAGE_BASE%:latest ^
            .
        if errorlevel 1 (
            echo [ERROR] docker build failed.
            exit /b 1
        )
    )
) else (
    echo [ERROR] BUILD_PUSH_MODE must be docker or buildx.
    exit /b 1
)

if /I "%BUILD_PUSH_MODE%"=="docker" (
    echo.
    echo [4/7] Pushing %IMAGE_BASE%:%RELEASE_VERSION%...
    if defined DRY_RUN (
        echo DRY-RUN: docker push %IMAGE_BASE%:%RELEASE_VERSION%
    ) else (
        docker push %IMAGE_BASE%:%RELEASE_VERSION%
        if errorlevel 1 (
            echo [ERROR] docker push %IMAGE_BASE%:%RELEASE_VERSION% failed.
            exit /b 1
        )
    )

    echo.
    echo [5/7] Pushing %IMAGE_BASE%:latest...
    if defined DRY_RUN (
        echo DRY-RUN: docker push %IMAGE_BASE%:latest
    ) else (
        docker push %IMAGE_BASE%:latest
        if errorlevel 1 (
            echo [ERROR] docker push %IMAGE_BASE%:latest failed.
            exit /b 1
        )
    )
) else (
    echo.
    echo [4/7] Push completed by buildx.
    echo [5/7] Push completed by buildx.
)

if "%SKIP_GIT%"=="1" (
    echo.
    echo [6/7] Skipping git commit/tag/push.
) else (
    echo.
    echo [6/7] Creating git commit and tag...
    if defined DRY_RUN (
        echo DRY-RUN: git add VERSION
        echo DRY-RUN: git commit -m "chore: release v%RELEASE_VERSION%"
        echo DRY-RUN: git tag v%RELEASE_VERSION%
    ) else (
        git add VERSION
        git commit -m "chore: release v%RELEASE_VERSION%"
        if errorlevel 1 (
            echo [WARN] git commit failed. It may already be committed or there may be unrelated changes.
        )
        git tag v%RELEASE_VERSION% 2>nul
        if errorlevel 1 (
            echo [WARN] tag v%RELEASE_VERSION% already exists or could not be created.
        )
    )
)

if "%SKIP_GIT%"=="1" (
    echo [7/7] Skipping git push.
) else (
    echo.
    echo [7/7] Pushing git branch and tag...
    if defined DRY_RUN (
        echo DRY-RUN: git push
        echo DRY-RUN: git push origin v%RELEASE_VERSION%
    ) else (
        git push
        if errorlevel 1 echo [WARN] git push failed.
        git push origin v%RELEASE_VERSION%
        if errorlevel 1 echo [WARN] git tag push failed.
    )
)

echo.
echo ============================================
echo Done: v%RELEASE_VERSION%
echo Image: %IMAGE_BASE%:%RELEASE_VERSION%
echo Image: %IMAGE_BASE%:latest
echo Server update:
echo   docker login
echo   docker compose pull ^&^& docker compose up -d
echo ============================================
echo.

endlocal
