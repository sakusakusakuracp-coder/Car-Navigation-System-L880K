"""設定・音楽・録画一覧・警告の操作を非同期で処理するUI窓口。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import socket
from time import monotonic

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from telemetry_ipc import runtime_socket_path
from ui.config.preferences import Preferences, DEFAULTS
from ui.config.file_lock import FileLock
from ui.controllers.event_ordering import OrderedEventGate
from ui.controllers.mopidy_client import MopidyClient
from ui.controllers.audio_output import AudioOutputMonitor
from ui.controllers.official_music import OfficialMusicController, SERVICES
from ui.controllers.recording_library import RecordingLibrary
from ui.state.warnings import WarningHistory
from ui.controllers.error_messages import task_failure


def socket_request(path, request):
    """設定操作を有限時間で1回だけ送り、結果不明時には再送しない。"""
    if not hasattr(socket, "AF_UNIX"):
        raise OSError("この環境ではLinuxサービスへ接続できません")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(path)
        client.sendall((json.dumps(request) + "\n").encode())
        with client.makefile("rb") as reader:
            line = reader.readline(65537)
    if len(line) > 65536 or not line.endswith(b"\n"):
        raise ValueError("サービスから有効な応答が届きませんでした")
    result = json.loads(line)
    if not isinstance(result, dict) or result.get("accepted") is not True:
        raise ValueError(result.get("reason", "操作が拒否されました") if isinstance(result, dict) else "応答形式が不正です")
    return result


class FeatureController(QObject):
    changed = Signal()
    completed = Signal(str, object, str)

    def __init__(self, state, bridge, config, preferences=None):
        """表示状態はQtスレッド、通信とファイル処理は有限数の実行枠で管理する。"""
        super().__init__()
        self.state, self.bridge, self.config = state, bridge, config
        self.preferences = preferences or Preferences(config.get("preferences_path"))
        self.instance_lock = FileLock(str(self.preferences.path) + ".ui.lock")
        self.instance_lock.__enter__()
        self.history = WarningHistory()
        self.history_path = self.preferences.path.with_name(self.preferences.path.stem + ".warnings.json")
        self.history_saved_revision = 0
        self.history_retry_at = 0
        self.history_writable = True
        try:
            self.history.load(self.history_path)
        except (OSError, ValueError, UnicodeError):
            self.history_writable = False
            self.history.update("ui.history", "警告履歴を読み込めません。既存ファイルは上書きせず、新しい履歴は今回の起動中だけ保持します")
        self.notice_text = ""
        try:
            self.saved = self.preferences.load()
        except (OSError, ValueError):
            self.saved = dict(DEFAULTS)
            self.history.update("ui.settings", "設定を読み込めないため初期値で起動しました")
        self.editing = dict(self.saved)
        self.jobs = set()
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ui-services")
        self.closed = False
        self.audio_client = MopidyClient(config.get("mopidy_url", "http://127.0.0.1:6680/mopidy/rpc"))
        self.web_music = OfficialMusicController(state, self)
        self.web_after_pause = ""
        self.web_music.changed.connect(self._cancel_abandoned_web_open)
        self.audio_value = {"connected": False, "title": "未接続", "tracks": [], "position": 0, "length": 0, "volume": None}
        self.library_items = []
        self.library_page = 0
        self.library_locations = []
        self.library_label = "ライブラリ"
        self.playlists_value = []
        self.playlist_original = None
        self.playlist_editing = {}
        self.output_monitor = AudioOutputMonitor(config.get("audio_sink_name", ""))
        self.output_value = {"status": "unknown", "label": "USB音声出力: 確認中", "devices": []}
        self.output_deadline = 0
        self.audio_deadline = 0
        self.music_generation = 0
        self.poll_generation = 0
        self.fan_value = {"status": "未接続", "temperature": "—", "rpm": "—", "duty": "—"}
        self.fan_deadline = 0
        self.gate = OrderedEventGate()
        self.camera_value = {}
        self.recording_list = {"items": [], "more": False, "page": 0}
        self.playback_url = ""
        self.playback_generation = 0
        self.recordings = RecordingLibrary(config.get("camera_config_path", str(Path.home() / ".config/l880k-car-navigation/camera.json")))
        self.camera_socket = config.get("camera_socket_path", os.environ.get("L880K_CAMERA_SOCKET", runtime_socket_path("l880k-camera.sock")))
        self.drive_socket = config.get("drive_upload_socket_path", os.environ.get("L880K_DRIVE_SOCKET", runtime_socket_path("l880k-drive-upload.sock")))
        self.completed.connect(self._complete)
        state.changed.connect(self._update_warnings)
        bridge.service_event.connect(self.on_event)
        bridge.audio_controller = self
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll)

    @Property(bool, notify=changed)
    def parked(self):
        """車速通知が有効な停車状態でのみ設定・録画再生を許可する。"""
        return self.state.stationaryConfirmed

    @Property(bool, notify=changed)
    def operationAllowed(self):
        """停車確認済み、またはOBD2車速を取得できない場合の操作許可を返す。"""
        return self.parked or self.state.obdUnavailable

    @Property(str, notify=changed)
    def drivingRestriction(self):
        """操作許可の根拠と、OBD2未取得時の注意を画面へ返す。"""
        if self.parked:
            return ""
        if self.state.obdUnavailable:
            return "OBD2車速を取得できないため、停車確認なしで操作を許可しています"
        return "走行中のため、設定変更・録画再生は利用できません"

    def _require_parked(self):
        """QML以外から呼ばれても停車またはOBD2未取得の条件を適用する。"""
        if self.operationAllowed:
            return True
        self.notice_text = self.drivingRestriction
        self.changed.emit()
        return False

    def _flush_history(self):
        """変更時だけ履歴を非同期保存し、失敗時の毎秒再試行を避ける。"""
        if (not self.history_writable or self.history.revision == self.history_saved_revision
                or monotonic() < self.history_retry_at or "history_save" in self.jobs):
            return
        revision, snapshot = self.history.revision, self.history.snapshot()
        def save():
            """Qt側の履歴を参照せず、受付時のコピーと版番号を保存する。"""
            self.history.save(self.history_path, snapshot)
            return revision
        self.submit("history_save", save, quiet=True)

    @Property("QVariantMap", notify=changed)
    def settings(self):
        """適用済み設定を表示と起動処理に公開する。"""
        return dict(self.saved)

    @Property("QVariantMap", notify=changed)
    def draft(self):
        """取消可能な編集中の設定を返す。"""
        return dict(self.editing)

    @Property("QVariantList", notify=changed)
    def busy(self):
        """操作種別ごとに二重送信を防ぐため実行中の一覧を返す。"""
        return sorted(self.jobs)

    @Property(str, notify=changed)
    def notice(self):
        """受付・完了・失敗を区別した最新操作結果を返す。"""
        return self.notice_text

    @Property("QVariantMap", notify=changed)
    def audio(self):
        """実際のMopidy状態だけを返す。"""
        return dict(self.audio_value)

    @Property(QObject, constant=True)
    def webMusic(self):
        """公式音楽画面の起動・終了だけを扱う窓口を公開する。"""
        return self.web_music

    @Slot()
    def open_official_music(self):
        """観測中のMopidy再生を一時停止してから公式ブラウザを開く。"""
        if self.web_music.source not in SERVICES or not self._require_parked() or "audio_command" in self.jobs:
            return
        if self.audio_value.get("connected") and self.audio_value.get("state") == "playing":
            self.web_after_pause = self.web_music.source
            self.music("pause")
        else:
            self.web_music.open()

    def _cancel_abandoned_web_open(self):
        """Mopidy停止の待機中に音源・画面・停車条件が変わったら起動予約を破棄する。"""
        if self.web_after_pause and (self.web_music.source != self.web_after_pause
                or self.state.screen != "audio" or not self.operationAllowed):
            self.web_after_pause = ""

    @Property("QVariantList", notify=changed)
    def musicLibrary(self):
        """Mopidyが返したフォルダ・曲を返す。"""
        return self.library_items[self.library_page * 50:(self.library_page + 1) * 50]

    @Property("QVariantMap", notify=changed)
    def musicPage(self):
        """全取得結果のページ情報を返す。500件以降も切り捨てない。"""
        return {"page": self.library_page, "total": len(self.library_items),
                "more": (self.library_page + 1) * 50 < len(self.library_items),
                "back": len(self.library_locations) > 1, "label": self.library_label}

    @Property("QVariantList", notify=changed)
    def playlists(self):
        """保存済みプレイリストの一覧を返す。"""
        return self.playlists_value

    @Property("QVariantMap", notify=changed)
    def playlistDraft(self):
        """保存前の名前・曲順を表示する。確定済みデータとは別に保持する。"""
        return deepcopy(self.playlist_editing)

    @Property("QVariantMap", notify=changed)
    def audioOutput(self):
        """USB出力の存在を表示する。Mopidyの経路や発音成功を意味しない。"""
        return dict(self.output_value)

    @Property("QVariantMap", notify=changed)
    def fan(self):
        """期限内の1台の冷却ファン観測値を返す。"""
        return dict(self.fan_value)

    @Property("QVariantMap", notify=changed)
    def cameraSettings(self):
        """録画サービスが読み戻した設定を返す。"""
        return dict(self.camera_value)

    @Property("QVariantMap", notify=changed)
    def recordingsPage(self):
        """日付・方向で抽出したページを返す。"""
        return self.recording_list

    @Property(str, notify=changed)
    def playbackSource(self):
        """再生用コピーのURLを返す。元録画のパスをQMLへ渡さない。"""
        return self.playback_url

    @Property("QVariantList", notify=changed)
    def warnings(self):
        """発生・確認・回復を区別した警告一覧を返す。"""
        return [dict(x) for x in self.history.items]

    @Property("QVariantMap", notify=changed)
    def banner(self):
        """現在表示すべき最優先の未確認警告を返す。"""
        return dict(self.history.banner)

    def start(self):
        """音楽状態の定期取得を開始する。サービスを勝手に起動しない。"""
        self.timer.start()
        self.poll()

    def submit(self, name, work, quiet=False):
        """処理中の同種操作を拒否し、通信・保存で画面操作を止めない。"""
        if self.closed or name in self.jobs:
            return
        for group in ({"preferences", "restore", "backup"}, {"camera_read", "camera_apply"},
                      {"playlist_read", "playlist_save", "playlist_create", "playlist_delete", "playlist_append"}):
            if name in group and self.jobs & group:
                return
        self.jobs.add(name)
        if not quiet:
            self.notice_text = "処理中…"
        self.changed.emit()
        future = self.pool.submit(work)

        def done(task):
            """結果をQtスレッドへ戻し、終了済み画面には通知しない。"""
            try:
                result, error = task.result(), ""
            except Exception as exc:
                result, error = None, str(exc) or type(exc).__name__
            if not self.closed:
                self.completed.emit(name, result, error)
        future.add_done_callback(done)

    @Slot(str, object, str)
    def _complete(self, name, result, error):
        """要求ごとの完了を反映し、失敗時に適用済み値を上書きしない。"""
        self.jobs.discard(name)
        if self.closed:
            return
        if name == "history_save":
            if error:
                self.history_retry_at = monotonic() + 30
                self.notice_text = "警告履歴を保存できません: " + error
            else:
                self.history_saved_revision = result
            self.changed.emit()
            return
        if name == "audio_output":
            self.output_value = result if not error else {"status": "unknown", "label": "USB音声出力: 確認できません", "devices": []}
            self.output_deadline = monotonic() + 5
            status = self.output_value["status"]
            if status in {"connected", "disconnected"}:
                self.history.update("audio.output", "指定したUSB音声出力が接続されていません", status == "disconnected")
            self.changed.emit()
            return
        if name == "audio_poll" and self.poll_generation != self.music_generation:
            self.changed.emit()
            return
        if error:
            if name == "audio_poll":
                self.audio_value.update(connected=False, title="未接続", tracks=[], position=0, length=0)
            else:
                self.notice_text = task_failure(name, error) + "\n通信切断時は、操作が反映されたか確認してください。"
                self.history.update("operation." + name, self.notice_text)
            if name == "camera_read":
                self.camera_value = {}
        else:
            if name in {"preferences", "restore"}:
                self.saved = dict(result)
                self.editing = dict(result)
            elif name in {"audio_poll", "audio_command"}:
                self.audio_value = result
                self.audio_deadline = monotonic() + 5
            elif name == "browse":
                items, locations, label = result
                self.library_items, self.library_locations, self.library_label = items, locations, label
                self.library_page = 0
            elif name == "playlists":
                self.playlists_value = result
            elif name in {"playlist_read", "playlist_save", "playlist_create"}:
                self.playlist_original = deepcopy(result)
                self.playlist_editing = deepcopy(result)
                if name != "playlist_read":
                    self.read_playlists()
            elif name == "playlist_delete":
                self.playlist_original, self.playlist_editing = None, {}
                self.read_playlists()
            elif name == "playlist_append":
                self.playlist_editing["tracks"] = self.playlist_editing.get("tracks", []) + result
            elif name in {"camera_read", "camera_apply"}:
                self.camera_value = result["settings"]
            elif name == "recordings":
                self.recording_list = result
            elif name == "playback":
                generation, path = result
                if generation != self.playback_generation or not self.operationAllowed:
                    self.changed.emit()
                    return
                self.playback_url = QUrl.fromLocalFile(path).toString()
            if name != "audio_poll":
                self.notice_text = "完了しました"
                self.history.update("operation." + name, "", False)
                if name == "camera_apply":
                    self.notice_text = "設定を保存・適用しました。録画中のファイルは終了処理後に停止します"
                elif name == "drive":
                    self.notice_text = "送信の一時停止・再開を保存しました" if result.get("enabled") else "送信設定が無効です。認証と送信先の設定を確認してください"
        if name == "audio_command" and self.web_after_pause:
            provider, self.web_after_pause = self.web_after_pause, ""
            if (not error and result.get("state") in {"paused", "stopped"} and self.state.screen == "audio"
                    and self.operationAllowed and self.web_music.source == provider):
                self.web_music.open()
        self._update_warnings()
        self.changed.emit()

    @Slot()
    def poll(self):
        """観測値の失効を処理し、音楽照会の重複を抑える。"""
        now = monotonic()
        if self.audio_deadline and now > self.audio_deadline:
            self.audio_value.update(connected=False, title="更新停止", tracks=[])
        if self.fan_deadline and now > self.fan_deadline:
            self.fan_deadline = 0
            self.fan_value = {"status": "更新停止", "temperature": "—", "rpm": "—", "duty": "—"}
            self.history.update("fan.connection", "冷却状態の通知が途絶えました")
        if not {"audio_command", "audio_poll"} & self.jobs:
            self.poll_generation = self.music_generation
            self.submit("audio_poll", self.audio_client.status, quiet=True)
        if self.output_deadline and now > self.output_deadline:
            self.output_value = {"status": "unknown", "label": "USB音声出力: 更新停止", "devices": []}
        self.submit("audio_output", self.output_monitor.status, quiet=True)
        self._flush_history()
        self.changed.emit()

    @Slot(str, "QVariant")
    def edit(self, key, value):
        """編集中の値だけを更新し、適用操作まで保存しない。"""
        if self._require_parked() and key in DEFAULTS and key != "schema_version" and not {"preferences", "restore"} & self.jobs:
            self.editing = {**self.editing, key: value}
            self.changed.emit()

    @Slot()
    def apply(self):
        """設定検証と原子的保存が成功してから、画面の確定値を変更する。"""
        if not self._require_parked():
            return
        value = dict(self.editing)
        self.submit("preferences", lambda: self.preferences.save(value))

    @Slot()
    def cancel(self):
        """適用前の編集値を確定済みの状態へ戻す。"""
        self.editing = dict(self.saved)
        self.notice_text = "編集を取り消しました"
        self.changed.emit()

    @Slot()
    def defaults(self):
        """初期値を編集欄へ入れる。適用前に確認できるよう即保存しない。"""
        if not self._require_parked():
            return
        self.editing = dict(DEFAULTS)
        self.changed.emit()

    @Slot()
    def backup(self):
        """確定済み設定だけを固定バックアップ先へ保存する。"""
        value = dict(self.saved)
        self.submit("backup", lambda: self.preferences.save(value, backup=True))

    @Slot()
    def restore(self):
        """検証したバックアップを通常設定へ保存して復元する。"""
        if self._require_parked():
            self.submit("restore", lambda: self.preferences.save(self.preferences.load(backup=True)))

    @Slot()
    def reload_preferences(self):
        """未保存の編集を破棄し、競合後にディスクの設定を再取得する。"""
        if self._require_parked():
            self.submit("preferences", self.preferences.load)

    @Slot(str, "QVariant")
    def music(self, action, value=None):
        """連打を抑え、音楽操作後の実状態を読み戻す。"""
        if self.closed or "audio_command" in self.jobs:
            return
        if action not in {"pause", "stop"} and (self.web_music.source != "local" or self.web_music.closing):
            self.notice_text = "保存済み音楽を選択し、公式ブラウザの終了後に操作してください"
            self.changed.emit()
            return
        self.music_generation += 1
        self.submit("audio_command", lambda: self.audio_client.command(action, value))

    @Slot(str)
    def browse(self, uri=""):
        """フォルダ内の実曲を非同期で取得する。"""
        locations = self.library_locations + [uri] if uri else [""]
        self.submit("browse", lambda: (self.audio_client.browse(uri), locations, "ライブラリ"))

    @Slot(str)
    def search_music(self, text):
        """検索結果もフォルダ一覧と同じ50件単位で表示する。"""
        locations = list(self.library_locations)
        self.submit("browse", lambda: (self.audio_client.search(text), locations, "検索: " + text.strip()))

    @Slot()
    def music_back(self):
        """フォルダ履歴を一段戻す。取得失敗時は現在の一覧を維持する。"""
        if len(self.library_locations) > 1:
            locations = self.library_locations[:-1]
            self.submit("browse", lambda: (self.audio_client.browse(locations[-1]), locations, "ライブラリ"))

    @Slot(int)
    def music_page(self, page):
        """取得済み一覧をページ切替し、ネットワークへ不要な再照会をしない。"""
        if 0 <= page <= max(0, (len(self.library_items) - 1) // 50):
            self.library_page = page
            self.changed.emit()

    @Slot()
    def read_playlists(self):
        """保存済みプレイリストを非同期取得する。"""
        self.submit("playlists", self.audio_client.playlists)

    @Slot(str)
    def read_playlist(self, uri):
        """選択したプレイリストの確定値と編集用コピーを取得する。"""
        self.submit("playlist_read", lambda: self.audio_client.playlist(uri))

    def _playlist_edit_allowed(self):
        """保存中の編集を抑止し、停車中のみ曲順や名前を変更する。"""
        return self._require_parked() and not any(x.startswith("playlist_") for x in self.jobs)

    @Slot(str)
    def rename_playlist(self, name):
        """名前を編集用コピーだけに反映する。保存操作まで送信しない。"""
        if self._playlist_edit_allowed() and self.playlist_original:
            self.playlist_editing["name"] = name
            self.changed.emit()

    @Slot(int, int)
    def move_playlist_track(self, source, destination):
        """曲を指定位置へ移動する。負の移動先は編集リストからの除外を意味する。"""
        if not self._playlist_edit_allowed():
            return
        tracks = self.playlist_editing.get("tracks", [])
        if 0 <= source < len(tracks) and -1 <= destination < len(tracks):
            track = tracks.pop(source)
            if destination >= 0:
                tracks.insert(destination, track)
            self.changed.emit()

    @Slot()
    def append_playlist_queue(self):
        """現在の再生キューを編集中リストの末尾へ追加する。自動保存はしない。"""
        if self._playlist_edit_allowed() and self.playlist_original:
            self.submit("playlist_append", self.audio_client.queue_tracks)

    @Slot()
    def save_playlist(self):
        """受付時のコピーを保存し、失敗時は編集内容を残す。"""
        if self._playlist_edit_allowed() and self.playlist_original:
            original, edited = deepcopy(self.playlist_original), deepcopy(self.playlist_editing)
            self.submit("playlist_save", lambda: self.audio_client.save_playlist(original, edited))

    @Slot(str)
    def create_playlist(self, name):
        """停車中に空のプレイリストを作成する。再試行は利用者操作に限る。"""
        if self._playlist_edit_allowed():
            self.submit("playlist_create", lambda: self.audio_client.create_playlist(name))

    @Slot()
    def delete_playlist(self):
        """確認済みの選択対象だけを削除する。音源ファイルは削除しない。"""
        if self._playlist_edit_allowed() and self.playlist_original:
            original = deepcopy(self.playlist_original)
            self.submit("playlist_delete", lambda: self.audio_client.delete_playlist(original))

    @Slot()
    def read_camera_settings(self):
        """サービスの保存済み設定を取得する。推測の初期値は表示しない。"""
        self.submit("camera_read", lambda: socket_request(self.camera_socket, {"operation": "settings"}))

    @Slot(bool, int)
    def apply_camera(self, enabled, seconds):
        """録画設定だけをサービスへ渡し、成功応答で表示を確定する。"""
        if not self._require_parked():
            return
        self.submit("camera_apply", lambda: socket_request(self.camera_socket, {"operation": "apply_settings",
                    "settings": {"recording_enabled": enabled, "segment_duration_s": seconds}}))

    @Slot(bool)
    def pause_upload(self, paused):
        """既存の送信許可条件を維持し、一時停止状態だけを永続化する。"""
        if self._require_parked():
            self.submit("drive", lambda: socket_request(self.drive_socket, {"operation": "pause" if paused else "resume"}))

    @Slot(str, str, int)
    def list_recordings(self, camera, date, page):
        """対象方向と日付を渡し、一覧をページ単位で更新する。"""
        self.submit("recordings", lambda: self.recordings.list(camera, date, page))

    @Slot(str)
    def play_recording(self, identifier):
        """前の再生を外し、元動画の削除と競合しないコピーを準備する。"""
        if not self._require_parked() or "playback" in self.jobs:
            return
        self.playback_url = ""
        self.playback_generation += 1
        generation = self.playback_generation
        self.changed.emit()
        self.submit("playback", lambda: (generation, self.recordings.prepare(identifier)))

    @Slot()
    def stop_playback(self):
        """画面を離れたとき、動画再生を終了する。"""
        self.playback_url = ""
        self.playback_generation += 1
        self.changed.emit()

    @Slot(str)
    def playback_error(self, text):
        """デコーダやファイルの再生エラーを利用者へ伝える。"""
        self.notice_text = "動画を再生できません: " + text
        self.changed.emit()

    @Slot(str)
    def acknowledge(self, identifier):
        """確認済みの警告も一覧に残し、原因解消と混同しない。"""
        self.history.acknowledge(identifier)
        self.changed.emit()

    @Slot(dict)
    def on_event(self, event):
        """ファン通知の順序と期限を検査し、実観測値を表示する。"""
        if event.get("fan_disconnected"):
            if self.fan_deadline:
                self.history.update("fan.connection", "冷却状態の通知が途絶えました")
            self.fan_deadline = 0
            self.fan_value = {"status": "未接続", "temperature": "—", "rpm": "—", "duty": "—"}
        elif event.get("event") == "fan.status":
            observed = event.get("observed_at_monotonic_ms")
            if (not {"schema_version", "boot_id", "sequence"} <= event.keys()
                    or type(observed) not in (int, float) or not math.isfinite(observed)
                    or event.get("status") not in {"RUNNING", "FAILSAFE", "DISABLED"}):
                return
            age = monotonic() - observed / 1000
            if age < -0.01 or age >= 5:
                return
            normalized = {**event, "schema_version": 1} if event.get("schema_version") == "1.0" else event
            if not self.gate.accept(normalized):
                return
            self.fan_deadline = observed / 1000 + 5
            def number(key, suffix):
                """未観測値を正常な0へ変換しない。"""
                value = event.get(key)
                return f"{value:.0f}{suffix}" if type(value) in (int, float) and math.isfinite(value) else "—"
            self.fan_value = {"status": {"RUNNING": "制御中", "FAILSAFE": "保護動作", "DISABLED": "無効"}.get(event.get("status"), "確認中"),
                              "temperature": number("temperature_c", " °C"), "rpm": number("fan_rpm", " rpm"),
                              "duty": f"{event['pwm_duty'] * 100:.0f} %" if type(event.get("pwm_duty")) in (int, float) and 0 <= event["pwm_duty"] <= 1 else "—"}
            self.history.update("fan.connection", "", False)
            self.history.update("fan.fault", "冷却ファンが保護動作中です: " + str(event.get("reason", "")), event.get("status") == "FAILSAFE", "critical")
        self.changed.emit()

    def _update_warnings(self):
        """実データから異常を判定する。測定失効を正常回復とは扱わない。"""
        self._cancel_abandoned_web_open()
        if not self.operationAllowed and (self.playback_url or "playback" in self.jobs):
            self.stop_playback()
        self.history.update("ui.operation", self.state.warning, bool(self.state.warning))
        for key, raw, threshold, high, message in (
            ("vehicle.coolant", self.state.coolantTemperature, self.saved["coolant_warning"], True, "エンジン水温が設定値を超えています"),
            ("vehicle.voltage", self.state.ecuVoltage, self.saved["voltage_warning"], False, "車両電圧が設定値を下回っています")):
            try:
                value = float(raw)
            except ValueError:
                continue
            self.history.update(key, message, value >= threshold if high else value <= threshold, "critical")
        storage = self.state.cameraStorage
        if storage in {"容量不足", "保存先異常"} or storage.startswith("空き"):
            self.history.update("camera.storage", "録画保存先: " + storage, not storage.startswith("空き"))
        for camera, value in self.state.cameraStates.items():
            if value.get("record_state") in {"FAULT", "RECORDING", "WAIT_INPUT"}:
                self.history.update("camera." + camera, camera + "の録画に失敗しました", value.get("record_state") == "FAULT")
        self.changed.emit()

    def close(self):
        """新規処理を止め、有限タイムアウトの通信完了後に再生コピーを除去する。"""
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.web_music.close()
        self.pool.shutdown(wait=True, cancel_futures=True)
        try:
            if self.history_writable:
                try:
                    self.history.save(self.history_path, self.history.snapshot())
                except OSError as exc:
                    import logging
                    logging.getLogger(__name__).error("警告履歴の終了時保存に失敗しました: %s", exc)
            self.recordings.close()
        except OSError:
            pass
        finally:
            self.instance_lock.__exit__()
