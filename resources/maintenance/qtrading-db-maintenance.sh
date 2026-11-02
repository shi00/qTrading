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
# 用户名白名单（与 sidecar maint.rs validate_username / Python _DATABASE_NAME_PATTERN 对齐）：
# [A-Za-z_][A-Za-z0-9_]*，长度 ≤ 63。只接受安全标识符，避免环境变量中的空格/通配符/引号
# 被 shell 拆成额外 argv（word-splitting / glob 展开）。
# 注意 shell case 的 `*` 是任意字符通配（非正则重复），此处用「禁用字符」反向判定。
case "$PG_USERNAME" in
    '' | *[!A-Za-z0-9_]* | [!A-Za-z_]*)
        echo "ERROR: invalid username (expected [A-Za-z_][A-Za-z0-9_]*, length <= 63): $PG_USERNAME" >&2
        exit 1
        ;;
esac
if [ "${#PG_USERNAME}" -gt 63 ]; then
    echo "ERROR: invalid username (expected [A-Za-z_][A-Za-z0-9_]*, length <= 63): $PG_USERNAME" >&2
    exit 1
fi

# 默认 postgres 时不传 --username，保持既有调用契约不变；自定义用户名始终作为独立、
# 加引号的 argv 追加，禁止经未加引号的字符串变量展开（word-splitting / glob）。
run_sidecar() {
    if [ "$PG_USERNAME" != "postgres" ]; then
        exec "$SIDECAR_BIN" "$@" --username "$PG_USERNAME"
    fi
    exec "$SIDECAR_BIN" "$@"
}

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
        run_sidecar dump --data-dir "$DEFAULT_DATA_DIR" --output "$1"
        ;;
    restore)
        if [ -z "$1" ]; then
            echo "Usage: $0 restore <input-file>" >&2
            exit 1
        fi
        run_sidecar restore --data-dir "$DEFAULT_DATA_DIR" --input "$1"
        ;;
    stop)
        exec "$SIDECAR_BIN" stop --data-dir "$DEFAULT_DATA_DIR" "$@"
        ;;
    maintenance-shell)
        run_sidecar maintenance-shell --data-dir "$DEFAULT_DATA_DIR" "$@"
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
