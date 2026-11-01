#!/bin/sh
# qTrading 离线维护脚本（Linux/macOS）
#
# 用途：qTrading 主程序无法启动时，直接调用 sidecar 进行数据库诊断/备份/恢复。
# 适用：仅 embedded variant 安装包（standard variant 不含 sidecar binary）。
#
# 详见 README-maintenance.md。

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SIDECAR_BIN="$SCRIPT_DIR/../../_internal/sidecars/qtrading-pg-sidecar"

if [ ! -x "$SIDECAR_BIN" ]; then
    echo "ERROR: sidecar binary not found or not executable: $SIDECAR_BIN" >&2
    echo "Please ensure qTrading was installed with the embedded variant." >&2
    exit 1
fi

# 超级用户名：与 sidecar --username 默认值一致（postgres）。
# 来源优先级：前置参数 --username <user> > 环境变量 QTRADING_PG_USERNAME > postgres。
# 非 postgres 时向 dump / restore / maintenance-shell 显式透传 --username
# （应用配置自定义了 embedded_pg_username 时必须设置，否则 sidecar 按默认超级用户连接）。
PG_USERNAME="${QTRADING_PG_USERNAME:-postgres}"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --username)
            if [ -z "$2" ]; then
                echo "ERROR: --username requires a value" >&2
                exit 1
            fi
            PG_USERNAME="$2"
            shift 2
            ;;
        --username=*)
            PG_USERNAME="${1#--username=}"
            shift
            ;;
        *)
            break
            ;;
    esac
done
if [ -z "$PG_USERNAME" ]; then
    echo "ERROR: username must not be empty" >&2
    exit 1
fi
# 默认 postgres 时不传 --username，保持既有调用契约不变
USERNAME_ARG=""
if [ "$PG_USERNAME" != "postgres" ]; then
    USERNAME_ARG="--username $PG_USERNAME"
fi

# 默认数据目录：platformdirs user_data_dir("qTrading")/postgres/16/data
# Linux: ~/.local/share/qTrading/postgres/16/data
# macOS: ~/Library/Application Support/qTrading/postgres/16/data
case "$(uname -s)" in
    Darwin*)
        DEFAULT_DATA_DIR="$HOME/Library/Application Support/qTrading/postgres/16/data"
        ;;
    Linux*)
        DEFAULT_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/qTrading/postgres/16/data"
        ;;
    *)
        DEFAULT_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/qTrading/postgres/16/data"
        ;;
esac

CMD="${1:-help}"
if [ "$#" -gt 0 ]; then
    shift
fi

case "$CMD" in
    status)
        exec "$SIDECAR_BIN" status --data-dir "$DEFAULT_DATA_DIR" "$@"
        ;;
    doctor)
        exec "$SIDECAR_BIN" doctor --data-dir "$DEFAULT_DATA_DIR" "$@"
        ;;
    dump)
        if [ -z "$1" ]; then
            echo "Usage: $0 dump <output-file>" >&2
            exit 1
        fi
        # $USERNAME_ARG 有意不加引号：默认空串（不传 --username），自定义时拆为两个参数
        exec "$SIDECAR_BIN" dump --data-dir "$DEFAULT_DATA_DIR" --output "$1" $USERNAME_ARG
        ;;
    restore)
        if [ -z "$1" ]; then
            echo "Usage: $0 restore <input-file>" >&2
            exit 1
        fi
        exec "$SIDECAR_BIN" restore --data-dir "$DEFAULT_DATA_DIR" --input "$1" $USERNAME_ARG
        ;;
    stop)
        exec "$SIDECAR_BIN" stop --data-dir "$DEFAULT_DATA_DIR" "$@"
        ;;
    maintenance-shell)
        exec "$SIDECAR_BIN" maintenance-shell --data-dir "$DEFAULT_DATA_DIR" $USERNAME_ARG "$@"
        ;;
    version)
        exec "$SIDECAR_BIN" version "$@"
        ;;
    help|--help|-h)
        cat <<EOF
qTrading Database Maintenance (offline)

Usage: $0 [--username <user>] <command> [args]

Commands:
  status                Show embedded PostgreSQL status
  doctor                Diagnose embedded PostgreSQL issues
  dump <file>           Dump database to file (PostgreSQL custom format)
  restore <file>        Restore database from file
  stop                  Stop running PostgreSQL (graded: smart > fast > kill)
  maintenance-shell     Start temporary maintenance instance
  version               Show sidecar version
  help                  Show this help

Options:
  --username <user>         Superuser name passed to dump/restore/maintenance-shell
                            (or set env QTRADING_PG_USERNAME; default: postgres)

Default data dir: $DEFAULT_DATA_DIR
Sidecar binary:   $SIDECAR_BIN

See README-maintenance.md for usage scenarios and safety notes.
EOF
        ;;
    *)
        echo "Unknown command: $CMD" >&2
        echo "Run '$0 help' for usage." >&2
        exit 1
        ;;
esac
