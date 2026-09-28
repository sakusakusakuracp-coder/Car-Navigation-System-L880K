#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${L880K_NAV_STATE_DIR:-/tmp/l880k-navigation-test}"
SOCKET_PATH="${L880K_NAV_SOCKET:-}"
OBD_SOCKET_PATH="${L880K_OBD_SOCKET:-/tmp/l880k-obd2.sock}"
POSITION_SOCKET_PATH="${L880K_POSITION_SOCKET:-/tmp/l880k-position-correction.sock}"
LIVI_SOCKET_PATH="${L880K_LIVI_SOCKET:-/tmp/l880k-livi.sock}"
SYSTEM_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

if [[ -z "$SOCKET_PATH" && -f "$STATE_DIR/socket.path" ]]; then
    SOCKET_PATH="$(<"$STATE_DIR/socket.path")"
fi
SOCKET_PATH="${SOCKET_PATH:-/tmp/l880k-navigation.sock}"
SESSION_STARTED=0
CONTAINER_STARTED=0

[[ -f "$STATE_DIR/session.started" ]] && SESSION_STARTED=1
[[ -f "$STATE_DIR/container.started" ]] && CONTAINER_STARTED=1

read_pid() {
    local name="$1"
    local path="$STATE_DIR/$name.pid"
    [[ -f "$path" ]] || return 0
    tr -cd '0-9' < "$path"
}

matches_process() {
    local pid="$1"
    local pattern="$2"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    [[ -r "/proc/$pid/cmdline" ]] || return 1
    tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq -- "$pattern"
}

stop_pid() {
    local name="$1"
    local pattern="$2"
    local pid
    pid="$(read_pid "$name")"
    if [[ -z "$pid" ]] || ! matches_process "$pid" "$pattern"; then
        return 0
    fi
    echo "$nameを停止します。PID=$pid"
    kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 0.25
    done
    if matches_process "$pid" "$pattern"; then
        echo "$nameが終了しないため強制終了します。PID=$pid" >&2
        kill -KILL "$pid" 2>/dev/null || true
    fi
}

stop_orphan_managers() {
    command -v pgrep >/dev/null 2>&1 || return 0
    local pattern="$ROOT_DIR/src/raspberry_pi5/waydroid_navigation_manager.py"
    local pid
    while read -r pid; do
        [[ -z "$pid" ]] && continue
        if matches_process "$pid" "$pattern"; then
            echo "PID記録のないナビ管理を停止します。PID=$pid"
            kill -TERM "$pid" 2>/dev/null || true
            for _ in $(seq 1 20); do
                kill -0 "$pid" 2>/dev/null || break
                sleep 0.25
            done
            if matches_process "$pid" "$pattern"; then
                kill -KILL "$pid" 2>/dev/null || true
            fi
        fi
    done < <(pgrep -f -- "$pattern" || true)
}

echo "L880K Debian 13テスト環境を安全に停止します。"
stop_pid "ui" "$ROOT_DIR/src/raspberry_pi5/main_app.py"
stop_pid "manager" "$ROOT_DIR/src/raspberry_pi5/waydroid_navigation_manager.py"
stop_pid "obd2" "$ROOT_DIR/src/raspberry_pi5/obd2_service.py"
stop_pid "position" "$ROOT_DIR/src/raspberry_pi5/position_correction_service.py"
stop_pid "livi" "$ROOT_DIR/src/raspberry_pi5/livi_integration_service.py"
stop_pid "session" "/usr/bin/waydroid session start"
stop_orphan_managers

if [[ "$SESSION_STARTED" -eq 1 ]]; then
    echo "テストで起動したWaydroidセッションを停止します。"
    env -u VIRTUAL_ENV PATH="$SYSTEM_PATH" /usr/bin/waydroid session stop >/tmp/l880k-waydroid-session-stop.log 2>&1 || true
fi
if [[ "$CONTAINER_STARTED" -eq 1 ]]; then
    echo "テストで起動したWaydroidコンテナを停止します。"
    sudo systemctl stop waydroid-container || true
fi

if [[ "$SOCKET_PATH" == /tmp/l880k-navigation*.sock || "$SOCKET_PATH" == "${XDG_RUNTIME_DIR:-/run/user/}/l880k-navigation"*.sock ]]; then
    if [[ -S "$SOCKET_PATH" ]]; then
        echo "残ったIPCソケットを削除します: $SOCKET_PATH"
        rm -f "$SOCKET_PATH"
    fi
fi

if [[ "$OBD_SOCKET_PATH" == /tmp/l880k-obd2*.sock || "$OBD_SOCKET_PATH" == "${XDG_RUNTIME_DIR:-/run/user/}/l880k-obd2.sock" ]]; then
    if [[ -S "$OBD_SOCKET_PATH" ]]; then
        echo "残ったOBD2ソケットを削除します: $OBD_SOCKET_PATH"
        rm -f "$OBD_SOCKET_PATH"
    fi
fi

if [[ "$POSITION_SOCKET_PATH" == /tmp/l880k-position-correction*.sock || "$POSITION_SOCKET_PATH" == "${XDG_RUNTIME_DIR:-/run/user/}/l880k-position-correction.sock" ]]; then
    if [[ -S "$POSITION_SOCKET_PATH" ]]; then
        echo "残った現在地補正ソケットを削除します: $POSITION_SOCKET_PATH"
        rm -f "$POSITION_SOCKET_PATH"
    fi
fi

if [[ "$LIVI_SOCKET_PATH" == /tmp/l880k-livi*.sock || "$LIVI_SOCKET_PATH" == "${XDG_RUNTIME_DIR:-/run/user/}/l880k-livi.sock" ]]; then
    if [[ -S "$LIVI_SOCKET_PATH" ]]; then
        echo "残ったLIVIソケットを削除します: $LIVI_SOCKET_PATH"
        rm -f "$LIVI_SOCKET_PATH"
    fi
fi

rm -f "$STATE_DIR/start.pid" "$STATE_DIR/manager.pid" "$STATE_DIR/obd2.pid" "$STATE_DIR/position.pid" "$STATE_DIR/livi.pid" "$STATE_DIR/session.pid" "$STATE_DIR/ui.pid" "$STATE_DIR/socket.path" "$STATE_DIR/session.started" "$STATE_DIR/container.started"
echo "テスト環境を停止しました。"
