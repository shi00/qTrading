#!/bin/sh
# qTrading 离线维护脚本（Linux/macOS）
#
# 用途：qTrading 主程序无法启动时，直接调用 sidecar 进行数据库诊断/备份/恢复。
# 适用：仅 embedded variant 安装包（standard variant 不含 sidecar binary）。
#
# 详见 README-maintenance.md。

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SIDECAR_BIN="$SCRIPT_DIR/../_internal/sidecars/qtrading-pg-sidecar"

if [ ! -x "$SIDECAR_BIN" ]; then
    echo "ERROR: sidecar binary not found or not executable: $SIDECAR_BIN" >&2
    echo "Please ensure qTrading was installed with the embedded variant." >&2
    exit 1
fi

# 默认数据目录：platformdirs user_data_dir("qTrading")/postgres/17/data
# Linux: ~/.local/share/qTrading/postgres/17/data
# macOS: ~/Library/Application Support/qTrading/postgres/17/data
case "$(uname -s)" in
    Darwin*)
        DEFAULT_DATA_DIR="$HOME/Library/Application Support/qTrading/postgres/17/data"
        ;;
    Linux*)
        DEFAULT_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/qTrading/postgres/17/data"
        ;;
    *)
        DEFAULT_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/qTrading/postgres/17/data"
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
        exec "$SIDECAR_BIN" dump --data-dir "$DEFAULT_DATA_DIR" --output "$1"
        ;;
    restore)
        if [ -z "$1" ]; then
            echo "Usage: $0 restore <input-file>" >&2
            exit 1
        fi
        exec "$SIDECAR_BIN" restore --data-dir "$DEFAULT_DATA_DIR" --input "$1"
        ;;
    stop)
        exec "$SIDECAR_BIN" stop --data-dir "$DEFAULT_DATA_DIR" "$@"
        ;;
    maintenance-shell)
        exec "$SIDECAR_BIN" maintenance-shell --data-dir "$DEFAULT_DATA_DIR" "$@"
        ;;
    version)
        exec "$SIDECAR_BIN" version "$@"
        ;;
    help|--help|-h)
        cat <<EOF
qTrading Database Maintenance (offline)

Usage: $0 <command> [args]

Commands:
  status                Show embedded PostgreSQL status
  doctor                Diagnose embedded PostgreSQL issues
  dump <file>           Dump database to file (PostgreSQL custom format)
  restore <file>        Restore database from file
  stop                  Stop running PostgreSQL (graded: smart > fast > kill)
  maintenance-shell     Start temporary maintenance instance
  version               Show sidecar version
  help                  Show this help

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
