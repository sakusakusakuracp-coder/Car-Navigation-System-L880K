#!/usr/bin/env bash
set -euo pipefail

TARGET_USER="${1:-${SUDO_USER:-${USER:-}}}"
SUDOERS_PATH="/etc/sudoers.d/l880k-waydroid-home"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "root権限で実行してください: sudo $0 [Linuxユーザー名]" >&2
    exit 1
fi
if [[ -z "$TARGET_USER" ]] || ! id "$TARGET_USER" >/dev/null 2>&1; then
    echo "有効なLinuxユーザー名を指定してください。" >&2
    exit 1
fi
if [[ ! -x /usr/bin/waydroid ]]; then
    echo "/usr/bin/waydroid が見つかりません。" >&2
    exit 1
fi

TEMP_FILE="$(mktemp)"
trap 'rm -f "$TEMP_FILE"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/bin/waydroid shell input keyevent KEYCODE_HOME\n' "$TARGET_USER" > "$TEMP_FILE"
chmod 0440 "$TEMP_FILE"
visudo -cf "$TEMP_FILE" >/dev/null
install -o root -g root -m 0440 "$TEMP_FILE" "$SUDOERS_PATH"
echo "Waydroid HOMEキー送信権限を設定しました: $SUDOERS_PATH"
