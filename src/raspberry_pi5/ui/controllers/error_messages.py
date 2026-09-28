"""操作失敗を、対象・操作・原因が分かる表示文へ変換する。"""

SERVICE_NAMES = {
    "02 Waydroidナビ管理": "OsmAnd（Waydroid）",
    "waydroid_navigation_manager": "OsmAnd（Waydroid）",
    "11 LIVI連携": "LIVI",
    "livi_integration_service": "LIVI",
    "09 OBD2車両情報取得": "OBD2車両情報",
    "obd2_service": "OBD2車両情報",
    "12 オーディオ連携": "音楽",
}

ACTION_NAMES = {
    "show_navigation": "画面表示", "show_livi": "画面表示", "show_waydroid_home": "Androidアプリ画面表示", "show": "画面表示",
    "hide_navigation": "画面を隠す操作", "hide_livi": "画面を隠す操作", "hide_waydroid_home": "Androidアプリ画面を隠す操作", "hide": "画面を隠す操作",
    "maintain_navigation_focus": "画面を前面へ戻す操作",
    "maintain_livi_focus": "画面を前面へ戻す操作", "maintain_waydroid_home_focus": "Androidアプリ画面を前面へ戻す操作", "focus": "画面を前面へ戻す操作",
    "prelaunch_navigation": "先行起動", "start_livi": "起動", "start": "起動",
    "stop_livi": "終了", "stop": "終了", "shutdown": "終了",
    "status_livi": "状態確認", "status": "状態確認",
    "set_polling_mode": "取得項目の切替", "play": "再生", "pause": "一時停止",
}

TASK_NAMES = {
    "preferences": "設定の保存・読込", "backup": "設定のバックアップ", "restore": "設定の復元",
    "audio_command": "音楽の再生操作", "audio_poll": "音楽の状態取得",
    "browse": "音楽ライブラリの読込・検索", "playlists": "プレイリスト一覧の取得",
    "playlist_read": "プレイリストの読込", "playlist_save": "プレイリストの保存",
    "playlist_create": "プレイリストの作成", "playlist_delete": "プレイリストの削除",
    "playlist_append": "プレイリストへの曲追加", "camera_read": "録画設定の読込",
    "camera_apply": "録画設定の適用", "drive": "Google Driveへの送信切替",
    "recordings": "録画一覧の取得", "playback": "録画の再生準備",
}


def failure_message(subject: str, reason: object) -> str:
    """一行目に失敗した操作、二行目以降に省略しない原因を記載する。"""
    detail = str(reason).strip() if reason is not None else ""
    return f"{subject}に失敗しました。\n原因: {detail or 'サービスから詳しい理由が届いていません。'}"


def command_failure(result: dict) -> str:
    """通信応答の発生元と操作名を残し、不明な識別子も捨てずに表示する。"""
    service = str(result.get("service") or "外部サービス")
    action = str(result.get("action") or result.get("operation") or "操作")
    if service in {"02 Waydroidナビ管理", "waydroid_navigation_manager"} and "waydroid_home" in action:
        subject = f"Waydroidの{ACTION_NAMES.get(action, action)}"
    else:
        subject = f"{SERVICE_NAMES.get(service, service)}の{ACTION_NAMES.get(action, action)}"
    return failure_message(subject, result.get("reason") or result.get("failure_reason"))


def task_failure(name: str, reason: object) -> str:
    """UIの非同期処理を利用者向けの名称に置き換えて失敗理由と組み合わせる。"""
    return failure_message(TASK_NAMES.get(name, f"処理（{name}）"), reason)
