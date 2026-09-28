#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SWAY_CONFIG_SOURCE="$ROOT_DIR/src/raspberry_pi5/systemd/sway/l880k-car-navigation-sway.conf"
START_SCRIPT="$ROOT_DIR/scripts/start_debian13_ui_test.sh"
TEMP_CONFIG=""
DESKTOP_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/l880k-car-navigation.desktop"

cleanup() {
    if [[ -n "$TEMP_CONFIG" ]]; then
        rm -f "$TEMP_CONFIG"
    fi
}
trap cleanup EXIT INT TERM

if ! command -v sway >/dev/null 2>&1; then
    echo "Swayがインストールされていません。次を実行してください: sudo apt install sway" >&2
    exit 1
fi
if ! command -v dbus-run-session >/dev/null 2>&1; then
    echo "dbus-run-sessionがありません。次を実行してください: sudo apt install dbus-daemon" >&2
    exit 1
fi
if [[ ! -f "$SWAY_CONFIG_SOURCE" ]]; then
    echo "Sway設定ファイルがありません: $SWAY_CONFIG_SOURCE" >&2
    exit 1
fi
if [[ -z "${WAYLAND_DISPLAY:-}" && -z "${DISPLAY:-}" ]]; then
    echo "既存のGUIセッションから実行してください。" >&2
    exit 1
fi

# Waydroidコンテナ操作に必要な認証を、端末が接続されている親シェルで先に済ませる。
# ネストしたSway内ではsudoがパスワード入力用端末を取得できないため、ここで更新する。
echo "ネストしたSwayを起動する前に管理者認証を行います。"
sudo -v
mkdir -p "$(dirname "$DESKTOP_FILE")"
if [[ ! -f "$DESKTOP_FILE" ]]; then
    printf '%s\n' \
        '[Desktop Entry]' \
        'Type=Application' \
        'Name=L880K Car Navigation' \
        'Exec=/usr/bin/true' \
        'NoDisplay=true' \
        'StartupNotify=false' \
        'StartupWMClass=l880k-car-navigation' \
        > "$DESKTOP_FILE"
fi
if command -v swaymsg >/dev/null 2>&1 && swaymsg -t get_version >/dev/null 2>&1; then
    exec "$START_SCRIPT"
fi

TEMP_CONFIG="$(mktemp /tmp/l880k-sway-config.XXXXXX)"
cat "$SWAY_CONFIG_SOURCE" > "$TEMP_CONFIG"
printf '\n# Debian 13テスト用の自動起動\nexec "%s"\n' "$START_SCRIPT" >> "$TEMP_CONFIG"

export L880K_NAV_IN_SWAY=1
export WLR_NO_HARDWARE_CURSORS=1
export XDG_CURRENT_DESKTOP=sway
export XDG_SESSION_DESKTOP=sway

# VirtualBoxでは互換性優先でPixmanを標準にする。3Dアクセラレーションを
# 有効化したゲストでは L880K_NAV_RENDERER=gles2 でGPU描画を試験できる。
case "${L880K_NAV_RENDERER:-pixman}" in
    pixman)
        export WLR_RENDERER_ALLOW_SOFTWARE=1
        export WLR_RENDERER=pixman
        ;;
    gles2)
        export WLR_RENDERER_ALLOW_SOFTWARE=0
        export WLR_RENDERER=gles2
        ;;
    auto)
        unset WLR_RENDERER
        export WLR_RENDERER_ALLOW_SOFTWARE=1
        ;;
    *)
        echo "未対応の描画方式です: ${L880K_NAV_RENDERER}" >&2
        echo "pixman、gles2、auto のいずれかを指定してください。" >&2
        exit 1
        ;;
esac
if [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
    export WLR_BACKENDS=wayland
else
    export WLR_BACKENDS=x11
fi

echo "ネストしたSwayを起動します。Swayウィンドウ内でUIとWaydroidを起動します。"
dbus-run-session sway --unsupported-gpu -c "$TEMP_CONFIG"
