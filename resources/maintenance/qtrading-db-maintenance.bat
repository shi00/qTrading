@echo off
setlocal enabledelayedexpansion

REM qTrading 离线维护脚本（Windows）
REM
REM 用途：qTrading 主程序无法启动时，直接调用 sidecar 进行数据库诊断/备份/恢复。
REM 适用：仅 embedded variant 安装包（standard variant 不含 sidecar binary）。
REM
REM 详见 README-maintenance.md。

set "SCRIPT_DIR=%~dp0"
set "SIDECAR_EXE=%SCRIPT_DIR%..\_internal\sidecars\qtrading-pg-sidecar.exe"

if not exist "%SIDECAR_EXE%" (
    echo ERROR: sidecar binary not found: %SIDECAR_EXE%
    echo Please ensure qTrading was installed with the embedded variant.
    exit /b 1
)

REM 默认数据目录：platformdirs user_data_dir("qTrading")/postgres/17/data
REM Windows: %LOCALAPPDATA%\qTrading\postgres\17\data
set "DEFAULT_DATA_DIR=%LOCALAPPDATA%\qTrading\postgres\17\data"

if "%~1"=="" goto :help

set "CMD=%~1"
shift /1

if /i "%CMD%"=="status" (
    "%SIDECAR_EXE%" status --data-dir "%DEFAULT_DATA_DIR%"
    exit /b !errorlevel!
)

if /i "%CMD%"=="doctor" (
    "%SIDECAR_EXE%" doctor --data-dir "%DEFAULT_DATA_DIR%"
    exit /b !errorlevel!
)

if /i "%CMD%"=="dump" (
    if "%~2"=="" (
        echo Usage: %0 dump ^<output-file^>
        exit /b 1
    )
    "%SIDECAR_EXE%" dump --data-dir "%DEFAULT_DATA_DIR%" --output "%~2"
    exit /b !errorlevel!
)

if /i "%CMD%"=="restore" (
    if "%~2"=="" (
        echo Usage: %0 restore ^<input-file^>
        exit /b 1
    )
    "%SIDECAR_EXE%" restore --data-dir "%DEFAULT_DATA_DIR%" --input "%~2"
    exit /b !errorlevel!
)

if /i "%CMD%"=="stop" (
    "%SIDECAR_EXE%" stop --data-dir "%DEFAULT_DATA_DIR%"
    exit /b !errorlevel!
)

if /i "%CMD%"=="maintenance-shell" (
    "%SIDECAR_EXE%" maintenance-shell --data-dir "%DEFAULT_DATA_DIR%"
    exit /b !errorlevel!
)

if /i "%CMD%"=="version" (
    "%SIDECAR_EXE%" version
    exit /b !errorlevel!
)

if /i "%CMD%"=="help" goto :help
if /i "%CMD%"=="/?" goto :help
if /i "%CMD%"=="-h" goto :help
if /i "%CMD%"=="--help" goto :help

echo Unknown command: %CMD%
goto :help

:help
echo qTrading Database Maintenance (offline)
echo.
echo Usage: %0 ^<command^> [args]
echo.
echo Commands:
echo   status                Show embedded PostgreSQL status
echo   doctor                Diagnose embedded PostgreSQL issues
echo   dump ^<file^>          Dump database to file (PostgreSQL custom format)
echo   restore ^<file^>       Restore database from file
echo   stop                  Stop running PostgreSQL (graded: smart ^> fast ^> kill)
echo   maintenance-shell     Start temporary maintenance instance
echo   version               Show sidecar version
echo   help                  Show this help
echo.
echo Default data dir: %DEFAULT_DATA_DIR%
echo Sidecar binary:   %SIDECAR_EXE%
echo.
echo See README-maintenance.md for usage scenarios and safety notes.
exit /b 0
