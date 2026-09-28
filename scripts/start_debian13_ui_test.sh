#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${L880K_NAV_CONFIG:-$ROOT_DIR/scripts/debian13-navigation-test.json}"
SOCKET_PATH="${L880K_NAV_SOCKET:-/tmp/l880k-navigation.sock}"
LOG_FILE="${L880K_NAV_LOG:-/tmp/l880k-navigation-manager.log}"
OBD_CONFIG_FILE="${L880K_OBD_CONFIG:-$ROOT_DIR/src/raspberry_pi5/config/obd2.example.toml}"
OBD_SOCKET_PATH="${L880K_OBD_SOCKET:-/tmp/l880k-obd2.sock}"
OBD_LOG_FILE="${L880K_OBD_LOG:-/tmp/l880k-obd2.log}"
POSITION_CONFIG_FILE="${L880K_POSITION_CONFIG:-$ROOT_DIR/src/raspberry_pi5/config/positioning.example.toml}"
POSITION_SOCKET_PATH="${L880K_POSITION_SOCKET:-/tmp/l880k-position-correction.sock}"
POSITION_LOG_FILE="${L880K_POSITION_LOG:-/tmp/l880k-position-correction.log}"
LIVI_CONFIG_FILE="${L880K_LIVI_CONFIG:-$ROOT_DIR/src/raspberry_pi5/config/livi.example.json}"
LIVI_SOCKET_PATH="${L880K_LIVI_SOCKET:-/tmp/l880k-livi.sock}"
LIVI_LOG_FILE="${L880K_LIVI_LOG:-/tmp/l880k-livi.log}"
STATE_DIR="${L880K_NAV_STATE_DIR:-/tmp/l880k-navigation-test}"
SYSTEM_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
UI_VENV="${L880K_UI_VENV:-}"
MANAGER_PID=""
OBD_PID=""
POSITION_PID=""
LIVI_PID=""
SESSION_PID=""
SESSION_LOG="/tmp/l880k-waydroid-session.log"
SESSION_STARTED_BY_SCRIPT=0
CONTAINER_STARTED_BY_SCRIPT=0
ANDROID_BOOT_TIMEOUT="${L880K_ANDROID_BOOT_TIMEOUT:-120}"

mkdir -p "$STATE_DIR"
printf '%s\n' "$$" > "$STATE_DIR/start.pid"
printf '%s\n' "$SOCKET_PATH" > "$STATE_DIR/socket.path"

cleanup() {
    set +e
    if [[ -n "$MANAGER_PID" ]] && kill -0 "$MANAGER_PID" 2>/dev/null; then
        kill -TERM "$MANAGER_PID"
        wait "$MANAGER_PID" 2>/dev/null
    fi
    if [[ -n "$OBD_PID" ]] && kill -0 "$OBD_PID" 2>/dev/null; then
        kill -TERM "$OBD_PID"
        wait "$OBD_PID" 2>/dev/null
    fi
    if [[ -n "$POSITION_PID" ]] && kill -0 "$POSITION_PID" 2>/dev/null; then
        kill -TERM "$POSITION_PID"
        wait "$POSITION_PID" 2>/dev/null
    fi
    if [[ -n "$LIVI_PID" ]] && kill -0 "$LIVI_PID" 2>/dev/null; then
        kill -TERM "$LIVI_PID"
        wait "$LIVI_PID" 2>/dev/null
    fi
    if [[ -n "$SESSION_PID" ]] && kill -0 "$SESSION_PID" 2>/dev/null; then
        kill -TERM "$SESSION_PID"
    fi
    rm -f "$STATE_DIR/start.pid" "$STATE_DIR/manager.pid" "$STATE_DIR/obd2.pid" "$STATE_DIR/position.pid" "$STATE_DIR/livi.pid" "$STATE_DIR/session.pid" "$STATE_DIR/ui.pid" "$STATE_DIR/socket.path" "$STATE_DIR/session.started" "$STATE_DIR/container.started"
}
trap cleanup EXIT INT TERM

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "設定ファイルがありません: $CONFIG_FILE" >&2
    exit 1
fi
if [[ -z "$UI_VENV" ]]; then
    for candidate in "${VIRTUAL_ENV:-}" "$ROOT_DIR/.venv" "$ROOT_DIR/.venv-waydroid" "$HOME/.venv" "$HOME/.venv-waydroid"; do
        [[ -z "$candidate" ]] && continue
        if [[ -f "$candidate/bin/activate" ]]; then
            UI_VENV="$candidate"
            break
        fi
    done
fi
if [[ -z "$UI_VENV" || ! -f "$UI_VENV/bin/activate" ]]; then
    echo "UI用の仮想環境が見つかりません。次のいずれかを作成してください:" >&2
    echo "  $ROOT_DIR/.venv" >&2
    echo "  $ROOT_DIR/.venv-waydroid" >&2
    echo "  $HOME/.venv-waydroid" >&2
    exit 1
fi
if [[ -z "${WAYLAND_DISPLAY:-}" || -z "${XDG_RUNTIME_DIR:-}" ]]; then
    echo "Waylandセッションが必要です。WAYLAND_DISPLAYとXDG_RUNTIME_DIRを確認してください。" >&2
    exit 1
fi
if grep -q '"display_backend"[[:space:]]*:[[:space:]]*"sway"' "$CONFIG_FILE"; then
    # UIをフルスクリーンにせず、Sway上でWaydroidを固定領域へ重ねる。
    export L880K_NAV_EXTERNAL_WINDOW=1
    SWAY_ACTIVE=0
    if command -v swaymsg >/dev/null 2>&1 && swaymsg -t get_version >/dev/null 2>&1; then
        SWAY_ACTIVE=1
    fi
    if [[ "$SWAY_ACTIVE" -ne 1 ]]; then
        if [[ "${L880K_NAV_IN_SWAY:-0}" != "1" ]]; then
            echo "ネストしたSwayを起動する前に管理者認証を行います。"
            sudo -v
            if ! sudo systemctl is-active --quiet waydroid-container; then
                printf '%s\n' "1" > "$STATE_DIR/container.started"
                sudo systemctl start waydroid-container
            fi
            if ! sudo systemctl is-active --quiet waydroid-container; then
                echo "Waydroidコンテナを起動できません。" >&2
                sudo systemctl status waydroid-container --no-pager >&2 || true
                exit 1
            fi
            export L880K_NAV_CONTAINER_PREPARED=1
            exec "$ROOT_DIR/scripts/start_debian13_sway_ui_test.sh"
        fi
        echo "Swayセッションに接続できません。" >&2
        exit 1
    fi
fi
if [[ ! -x /usr/bin/waydroid || ! -x /usr/bin/python3 ]]; then
    echo "/usr/bin/waydroid または /usr/bin/python3 がありません。" >&2
    exit 1
fi

if ! env -u VIRTUAL_ENV PATH="$SYSTEM_PATH" /usr/bin/python3 -c 'import dbus' >/dev/null 2>&1; then
    echo "システムPythonからdbusを読み込めません。次を実行してください:" >&2
    echo "  sudo apt install python3-dbus python3-gi gir1.2-glib-2.0" >&2
    exit 1
fi

waydroid_session_running() {
    env -u VIRTUAL_ENV PATH="$SYSTEM_PATH" /usr/bin/waydroid status \
        | grep -Eq '^Session:[[:space:]]+RUNNING[[:space:]]*$'
}

waydroid_android_boot_completed() {
    # `waydroid status`のSession: RUNNINGは、AndroidのSystemUIやアプリ起動が
    # 可能になったことを保証しない。shellはroot権限が必要なため、起動前に
    # sudo -vで認証を済ませ、sudo -nで対話入力なしに確認する。
    local boot_status
    boot_status="$(sudo -n env -u VIRTUAL_ENV PATH="$SYSTEM_PATH" \
        /usr/bin/waydroid shell getprop sys.boot_completed 2>/dev/null || true)"
    [[ "$boot_status" =~ (^|[[:space:]])1([[:space:]]|$) ]]
}

echo "Waydroidコンテナを起動します。"
if [[ "${L880K_NAV_CONTAINER_PREPARED:-0}" != "1" ]]; then
    if ! sudo systemctl is-active --quiet waydroid-container; then
        CONTAINER_STARTED_BY_SCRIPT=1
        printf '%s\n' "1" > "$STATE_DIR/container.started"
    fi
    sudo systemctl start waydroid-container
    if ! sudo systemctl is-active --quiet waydroid-container; then
        echo "Waydroidコンテナを起動できません。" >&2
        sudo systemctl status waydroid-container --no-pager >&2 || true
        exit 1
    fi
fi

if ! waydroid_session_running; then
    echo "Waydroidセッションを起動します。"
    SESSION_STARTED_BY_SCRIPT=1
    printf '%s\n' "1" > "$STATE_DIR/session.started"
    env -u VIRTUAL_ENV PATH="$SYSTEM_PATH" /usr/bin/waydroid session start >"$SESSION_LOG" 2>&1 &
    SESSION_PID=$!
    printf '%s\n' "$SESSION_PID" > "$STATE_DIR/session.pid"
fi

echo "Waydroidセッションの起動完了を待ちます。"
SESSION_READY=0
for _ in $(seq 1 60); do
    if waydroid_session_running; then
        SESSION_READY=1
        break
    fi
    sleep 1
done
if [[ "$SESSION_READY" -ne 1 ]]; then
    echo "Waydroidセッションが60秒以内に起動しませんでした。ログ: $SESSION_LOG" >&2
    cat "$SESSION_LOG" >&2 || true
    exit 1
fi

if ! sudo -n -v >/dev/null 2>&1; then
    echo "Androidの起動完了を確認するため、管理者認証を行います。"
    sudo -v
fi

echo "Androidの起動完了を待ちます。"
ANDROID_READY=0
for _ in $(seq 1 "$ANDROID_BOOT_TIMEOUT"); do
    if waydroid_android_boot_completed; then
        ANDROID_READY=1
        break
    fi
    sleep 1
done
if [[ "$ANDROID_READY" -ne 1 ]]; then
    echo "Androidが${ANDROID_BOOT_TIMEOUT}秒以内に起動完了しませんでした。" >&2
    echo "Waydroidセッションログ: $SESSION_LOG" >&2
    cat "$SESSION_LOG" >&2 || true
    echo "確認例: sudo waydroid shell getprop sys.boot_completed" >&2
    exit 1
fi
echo "Androidの起動完了を確認しました。"

if [[ -S "$SOCKET_PATH" ]]; then
    echo "既にIPCソケットがあります。管理プログラムの二重起動を確認してください: $SOCKET_PATH" >&2
    exit 1
fi

echo "Waydroidナビ管理を起動します。ログ: $LOG_FILE"
env -u VIRTUAL_ENV PATH="$SYSTEM_PATH" PYTHONPATH="$ROOT_DIR/src/raspberry_pi5" \
    /usr/bin/python3 "$ROOT_DIR/src/raspberry_pi5/waydroid_navigation_manager.py" \
    --config "$CONFIG_FILE" --socket "$SOCKET_PATH" >"$LOG_FILE" 2>&1 &
MANAGER_PID=$!
printf '%s\n' "$MANAGER_PID" > "$STATE_DIR/manager.pid"

for _ in $(seq 1 30); do
    if [[ -S "$SOCKET_PATH" ]]; then
        break
    fi
    if ! kill -0 "$MANAGER_PID" 2>/dev/null; then
        echo "Waydroidナビ管理が起動できません。ログを確認してください: $LOG_FILE" >&2
        cat "$LOG_FILE" >&2 || true
        exit 1
    fi
    sleep 0.2
done
if [[ ! -S "$SOCKET_PATH" ]]; then
    echo "IPCソケットが作成されませんでした: $SOCKET_PATH" >&2
    cat "$LOG_FILE" >&2 || true
    exit 1
fi

if [[ ! -f "$OBD_CONFIG_FILE" ]]; then
    echo "OBD2設定がありません。OBD2サービスを起動せずUIを続行します: $OBD_CONFIG_FILE" >&2
else
    echo "OBD2サービスを起動します。ログ: $OBD_LOG_FILE"
    env -u VIRTUAL_ENV PATH="$SYSTEM_PATH" PYTHONPATH="$ROOT_DIR/src/raspberry_pi5" \
        /usr/bin/python3 "$ROOT_DIR/src/raspberry_pi5/obd2_service.py" \
        --config "$OBD_CONFIG_FILE" --socket "$OBD_SOCKET_PATH" >"$OBD_LOG_FILE" 2>&1 &
    OBD_PID=$!
    printf '%s\n' "$OBD_PID" > "$STATE_DIR/obd2.pid"
    sleep 0.2
    if ! kill -0 "$OBD_PID" 2>/dev/null; then
        echo "OBD2サービスが終了しました。ログを確認してください: $OBD_LOG_FILE" >&2
        cat "$OBD_LOG_FILE" >&2 || true
    fi
fi

if [[ ! -f "$POSITION_CONFIG_FILE" ]]; then
    echo "現在地補正設定がありません。現在地補正サービスを起動せずUIを続行します: $POSITION_CONFIG_FILE" >&2
else
    echo "現在地補正サービスを起動します。ログ: $POSITION_LOG_FILE"
    env -u VIRTUAL_ENV PATH="$SYSTEM_PATH" PYTHONPATH="$ROOT_DIR/src/raspberry_pi5" \
        /usr/bin/python3 "$ROOT_DIR/src/raspberry_pi5/position_correction_service.py" \
        --config "$POSITION_CONFIG_FILE" --socket "$POSITION_SOCKET_PATH" >"$POSITION_LOG_FILE" 2>&1 &
    POSITION_PID=$!
    printf '%s\n' "$POSITION_PID" > "$STATE_DIR/position.pid"
    sleep 0.2
    if ! kill -0 "$POSITION_PID" 2>/dev/null; then
        echo "現在地補正サービスが終了しました。ログを確認してください: $POSITION_LOG_FILE" >&2
        cat "$POSITION_LOG_FILE" >&2 || true
    fi
fi

if [[ ! -f "$LIVI_CONFIG_FILE" ]]; then
    echo "LIVI設定がありません。LIVI連携サービスを起動せずUIを続行します: $LIVI_CONFIG_FILE" >&2
else
    echo "LIVI連携サービスを起動します。ログ: $LIVI_LOG_FILE"
    env -u VIRTUAL_ENV PATH="$SYSTEM_PATH" PYTHONPATH="$ROOT_DIR/src/raspberry_pi5" \
        /usr/bin/python3 "$ROOT_DIR/src/raspberry_pi5/livi_integration_service.py" \
        --config "$LIVI_CONFIG_FILE" --socket "$LIVI_SOCKET_PATH" >"$LIVI_LOG_FILE" 2>&1 &
    LIVI_PID=$!
    printf '%s\n' "$LIVI_PID" > "$STATE_DIR/livi.pid"
    for _ in $(seq 1 30); do
        if [[ -S "$LIVI_SOCKET_PATH" ]]; then
            break
        fi
        if ! kill -0 "$LIVI_PID" 2>/dev/null; then
            echo "LIVI連携サービスが起動できません。ログを確認してください: $LIVI_LOG_FILE" >&2
            cat "$LIVI_LOG_FILE" >&2 || true
            break
        fi
        sleep 0.2
    done
fi

source "$UI_VENV/bin/activate"
if ! python -c 'import PySide6' >/dev/null 2>&1; then
    echo "UI用仮想環境にPySide6がありません。次を実行してください:" >&2
    echo "  python -m pip install -r $ROOT_DIR/src/raspberry_pi5/requirements-ui.txt" >&2
    exit 1
fi
export PYTHONPATH="$ROOT_DIR/src/raspberry_pi5"
export L880K_NAV_SOCKET="$SOCKET_PATH"
export L880K_OBD_SOCKET="$OBD_SOCKET_PATH"
export L880K_POSITION_SOCKET="$POSITION_SOCKET_PATH"
export L880K_LIVI_SOCKET="$LIVI_SOCKET_PATH"
export QT_QPA_PLATFORM="wayland"

echo "UIを起動します。終了すると管理プログラムも終了します。"
python "$ROOT_DIR/src/raspberry_pi5/main_app.py" &
UI_PID=$!
printf '%s\n' "$UI_PID" > "$STATE_DIR/ui.pid"
wait "$UI_PID"
