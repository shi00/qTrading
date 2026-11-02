@echo off
setlocal enabledelayedexpansion

REM qTrading 离线维护脚本（Windows）
REM
REM 用途：qTrading 主程序无法启动时，直接调用 sidecar 进行数据库诊断/备份/恢复。
REM 适用：仅 embedded variant 安装包（standard variant 不含 sidecar binary）。
REM
REM 详见 README-maintenance.md。

set "SCRIPT_DIR=%~dp0"
set "SIDECAR_EXE=%SCRIPT_DIR%..\..\_internal\sidecars\qtrading-pg-sidecar.exe"

if not exist "%SIDECAR_EXE%" (
    echo ERROR: sidecar binary not found: %SIDECAR_EXE%
    echo Please ensure qTrading was installed with the embedded variant.
    exit /b 1
)

REM 默认数据目录：platformdirs user_data_dir("qTrading")/postgres/16/data
REM Windows: %LOCALAPPDATA%\qTrading\postgres\16\data
set "DEFAULT_DATA_DIR=%LOCALAPPDATA%\qTrading\postgres\16\data"

REM 超级用户名：与 sidecar --username 默认值一致（postgres）。
REM 非 postgres 时向 dump / restore / maintenance-shell 显式透传 --username
REM （应用配置自定义了 embedded_pg_username 时必须设置，否则 sidecar 按默认超级用户连接）。
set "PG_USERNAME=%QTRADING_PG_USERNAME%"
if not defined PG_USERNAME set "PG_USERNAME=postgres"

REM 用户名白名单（与 sidecar maint.rs validate_username / Python _DATABASE_NAME_PATTERN 对齐）：
REM [A-Za-z_][A-Za-z0-9_]*，长度 ≤ 63。只接受安全标识符，避免环境变量中的空格/引号/cmd
REM 元字符被解析为额外参数；校验后各调用点仍以引号包裹单个 argv。
set "PG_USERNAME_LEN=0"
set "PG_USERNAME_PROBE=!PG_USERNAME!"
:pg_username_length
if defined PG_USERNAME_PROBE (
    set "PG_USERNAME_PROBE=!PG_USERNAME_PROBE:~1!"
    set /a PG_USERNAME_LEN+=1
    goto :pg_username_length
)
if !PG_USERNAME_LEN! EQU 0 goto :pg_username_invalid
if !PG_USERNAME_LEN! GTR 63 goto :pg_username_invalid
if !PG_USERNAME_LEN! EQU 1 goto :pg_username_check_single
echo(!PG_USERNAME!| findstr /r /x "[A-Za-z_][A-Za-z0-9_]*" >nul
if errorlevel 1 goto :pg_username_invalid
goto :pg_username_valid
:pg_username_check_single
echo(!PG_USERNAME!| findstr /r /x "[A-Za-z_]" >nul
if errorlevel 1 goto :pg_username_invalid
goto :pg_username_valid
:pg_username_invalid
echo ERROR: invalid username: !PG_USERNAME! (expected [A-Za-z_][A-Za-z0-9_]*, length ^<= 63) 1>&2
exit /b 1
:pg_username_valid

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
    if "%~1"=="" (
        echo Usage: %0 dump ^<output-file^>
        exit /b 1
    )
    if not "%PG_USERNAME%"=="postgres" (
        "%SIDECAR_EXE%" dump --data-dir "%DEFAULT_DATA_DIR%" --output "%~1" --username "%PG_USERNAME%"
    ) else (
        "%SIDECAR_EXE%" dump --data-dir "%DEFAULT_DATA_DIR%" --output "%~1"
    )
    exit /b !errorlevel!
)

if /i "%CMD%"=="restore" (
    if "%~1"=="" (
        echo Usage: %0 restore ^<input-file^>
        exit /b 1
    )
    if not "%PG_USERNAME%"=="postgres" (
        "%SIDECAR_EXE%" restore --data-dir "%DEFAULT_DATA_DIR%" --input "%~1" --username "%PG_USERNAME%"
    ) else (
        "%SIDECAR_EXE%" restore --data-dir "%DEFAULT_DATA_DIR%" --input "%~1"
    )
    exit /b !errorlevel!
)

if /i "%CMD%"=="stop" (
    "%SIDECAR_EXE%" stop --data-dir "%DEFAULT_DATA_DIR%"
    exit /b !errorlevel!
)

if /i "%CMD%"=="maintenance-shell" (
    if not "%PG_USERNAME%"=="postgres" (
        "%SIDECAR_EXE%" maintenance-shell --data-dir "%DEFAULT_DATA_DIR%" --username "%PG_USERNAME%"
    ) else (
        "%SIDECAR_EXE%" maintenance-shell --data-dir "%DEFAULT_DATA_DIR%"
    )
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
echo Environment:
echo   QTRADING_PG_USERNAME  Superuser name passed as --username to dump/restore/maintenance-shell (default: postgres)
echo.
echo Default data dir: %DEFAULT_DATA_DIR%
echo Sidecar binary:   %SIDECAR_EXE%
echo.
echo See README-maintenance.md for usage scenarios and safety notes.
exit /b 0
