"""公式Webプレーヤーを専用Chromiumで開き、Sway上の表示だけを管理する。"""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Lock
from time import monotonic

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from ui.config.file_lock import FileLock


SERVICES = {
    "youtube_music": "https://music.youtube.com/",
    "spotify": "https://open.spotify.com/",
}

# ウィンドウの非表示化でサイト側の表示状態が変わると、再生が止まることがある。
# 専用ワークスペースへ退避してプロセスとページ状態を維持する。
HIDDEN_WORKSPACE = "__l880k_official_music_hidden"


def windows(node, workspace=""):
    """Swayの木をたどり、各ウィンドウと所属ワークスペースを返す。"""
    if node.get("type") == "workspace":
        workspace = node.get("name", "")
    if node.get("pid") and node.get("type") in {"con", "floating_con"}:
        yield node, workspace
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        yield from windows(child, workspace)


class OfficialMusicBrowser:
    """一つの専用ブラウザだけを所有し、画面切替中もページ状態を保持する。"""

    def __init__(self, profile_root=None):
        """認証情報をリポジトリ外の利用者専用プロファイルに分離する。"""
        self.root = Path(profile_root or Path.home() / ".config/l880k-car-navigation/web-music")
        self.process = None
        self.profile_lock = None
        self.provider = ""
        self.started = 0
        self.mapped = False

    @staticmethod
    def availability():
        """同じWaylandコンポジタで管理できる環境だけを許可する。"""
        if not sys.platform.startswith("linux") or not os.environ.get("SWAYSOCK") or not os.environ.get("WAYLAND_DISPLAY"):
            return "公式音楽画面にはLinuxのSwayセッションが必要です"
        if not shutil.which("swaymsg"):
            return "swaymsgが見つかりません"
        if not (shutil.which("chromium") or shutil.which("chromium-browser")):
            return "Chromiumが見つかりません"
        return ""

    @staticmethod
    def sway(*args):
        """シェルを介さず有限時間で実行し、Sway自身の失敗応答も検出する。"""
        result = subprocess.run(["swaymsg", "-r", *args], capture_output=True,
                                text=True, timeout=2, check=False)
        if result.returncode:
            raise RuntimeError("Swayとの通信に失敗しました")
        value = json.loads(result.stdout)
        if args[0] != "-t" and (not isinstance(value, list) or not value
                                or any(x.get("success") is not True for x in value)):
            raise RuntimeError("公式音楽画面の配置が拒否されました")
        return value

    def start(self, provider):
        """許可済みの公式URLだけを開き、他のブラウザプロセスへ合流しない。"""
        if provider not in SERVICES:
            raise ValueError("未対応の音楽サービスです")
        error = self.availability()
        if error:
            raise RuntimeError(error)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = FileLock(self.root / "browser.lock")
        lock.__enter__()
        self.profile_lock = lock
        profile = self.root / provider
        profile.mkdir(exist_ok=True, mode=0o700)
        executable = shutil.which("chromium") or shutil.which("chromium-browser")
        try:
            self.process = subprocess.Popen([
                executable, "--ozone-platform=wayland", "--no-first-run",
                "--no-default-browser-check", "--disable-background-mode",
                "--user-data-dir=" + str(profile), "--app=" + SERVICES[provider],
            ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.provider, self.started, self.mapped = provider, monotonic(), False
        except Exception:
            self.close()
            raise

    def reconcile(self, request, current):
        """最新の要求に一致する場合だけ起動・配置し、不要時は専用ワークスペースへ退避する。"""
        provider, visible, rect = request
        if self.process and self.process.poll() is not None:
            self.close()
            raise RuntimeError("公式ブラウザが終了しました。再度「開く」を押してください")
        if self.provider != provider:
            self.close()
        if not provider:
            return "停止中"
        if not self.process:
            if not visible or not current():
                return "待機中"
            self.start(provider)
        tree = self.sway("-t", "get_tree")
        items = list(windows(tree))
        # タイトルやChromeという名前では選ばず、起動したプロセスのPIDを照合する。
        owned = [(node, ws) for node, ws in items if node.get("pid") == self.process.pid]
        ui = next(((node, ws) for node, ws in items
                   if node.get("app_id") == "l880k-car-navigation" and node.get("pid") == os.getpid()), None)
        if not owned:
            if monotonic() - self.started > 20:
                raise RuntimeError("専用ブラウザの画面を確認できません。ChromiumとWayland接続を確認してください")
            return "ブラウザ起動中"
        if not visible or not current():
            for node, ws in owned:
                if ws != HIDDEN_WORKSPACE:
                    self.sway(f'[con_id={int(node["id"])}] move container to workspace {json.dumps(HIDDEN_WORKSPACE)}')
            return "画面非表示（再生状態を保持）"
        if not ui or ui[0].get("fullscreen_mode") or not ui[0].get("visible"):
            raise RuntimeError("UIの通常ウィンドウを確認できません。Sway用起動スクリプトを使用してください")
        owner, workspace = ui
        x, y, width, height = rect
        bounds = owner["rect"]
        width, height = min(width, bounds["width"] - x), min(height, bounds["height"] - y)
        if x < 0 or y < 0 or width < 100 or height < 100:
            raise RuntimeError("公式音楽の表示領域が未確定です")
        x, y = bounds["x"] + x, bounds["y"] + y
        for node, ws in owned:
            if not current():
                self.sway(f'[con_id={int(node["id"])}] move container to workspace {json.dumps(HIDDEN_WORKSPACE)}')
                continue
            commands = ["fullscreen disable", "floating enable", "border none"]
            if ws != workspace:
                commands.append("move container to workspace " + json.dumps(workspace))
            commands += [f"resize set {width} px {height} px", f"move absolute position {x} px {y} px"]
            # 表示切替のトグル操作は使わず、対象ワークスペースを明示する。
            if not self.mapped or ws != workspace or owner.get("focused"):
                commands.append("focus")
            self.sway(f'[con_id={int(node["id"])}] ' + ", ".join(commands))
        self.mapped = True
        return "公式画面を表示（ログイン・再生はサイト側）"

    def close(self):
        """自分で起動したブラウザだけを終了し、ログイン用プロファイルは残す。"""
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            self.process = None
        self.provider, self.mapped = "", False
        if self.profile_lock:
            self.profile_lock.__exit__()
            self.profile_lock = None


class OfficialMusicController(QObject):
    changed = Signal()
    completed = Signal(int, str, bool)

    def __init__(self, state, parent=None, browser=None):
        """Qt側の要求を一列の実行枠へ渡し、画面切替中の古い結果を捨てる。"""
        super().__init__(parent)
        self.state = state
        self.browser = browser or OfficialMusicBrowser()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="official-music")
        self.lock = Lock()
        self.selected = "local"
        self.target = ""
        self.rect = (0, 0, 0, 0)
        self.revision = 0
        self.request = ("", False, self.rect)
        self.message = ""
        self.pending = False
        self.pending_open = ""
        self.stopping = False
        self.closed = False
        self.completed.connect(self._complete)
        state.changed.connect(self._sync)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)

    @Property(str, notify=changed)
    def source(self):
        """Mopidyと公式Webサービスのどちらを表示するかを公開する。"""
        return self.selected

    @Property(str, notify=changed)
    def status(self):
        """ウィンドウ管理の結果だけを表示し、再生成功とは断定しない。"""
        return self.message

    @Property(bool, notify=changed)
    def running(self):
        """停止操作を有効にするため、起動要求があるかを返す。"""
        return bool(self.target)

    @Property(bool, notify=changed)
    def closing(self):
        """終了処理中に別の音源を再生しないため、停止完了までを区別する。"""
        return self.stopping

    @Slot(str)
    def select(self, source):
        """音源ボタンの選択後、公式サービスなら自動で画面を開く。"""
        if source not in {"local", *SERVICES}:
            return
        if source == self.selected:
            if source in SERVICES and not self.target and self.state.screen == "audio":
                self.open()
            return
        self.pending_open = source if source in SERVICES else ""
        self.stopping = bool(self.target) or self.stopping
        self.selected, self.target, self.message = source, "", ""
        self._sync()
        self.changed.emit()
        if not self.stopping and source in SERVICES:
            self.pending_open = ""
            self.open()

    @Slot()
    def open(self):
        """停車確認済み、またはOBD2未取得の代替状態で公式サイトを開く。"""
        if self.selected not in SERVICES:
            return
        if self.stopping:
            self.message = "前の公式ブラウザを終了しています"
            self.changed.emit()
            return
        error = self.browser.availability()
        if not (self.state.stationaryConfirmed or self.state.obdUnavailable):
            error = "走行中のため、公式画面を開けません"
        if error:
            self.message = error
            self.changed.emit()
            return
        self.target = self.selected
        self.message = "ブラウザ起動中"
        self._sync()
        self.changed.emit()

    @Slot()
    def stop(self):
        """ブラウザごと閉じる。サイトの一時停止ボタンとは区別する。"""
        self.pending_open = ""
        self.stopping = bool(self.target) or self.stopping
        self.target = ""
        self._sync()
        self.changed.emit()

    @Slot(int, int, int, int)
    def set_viewport(self, x, y, width, height):
        """QMLからウィンドウ内の論理ピクセル座標を受け取る。"""
        self.rect = (x, y, width, height)
        self._sync()

    @Slot()
    def _sync(self):
        """別画面・走行中・車速失効ではブラウザを隠し、音声再生はサイトに任せる。"""
        visible = bool(self.target) and self.state.screen == "audio" and (self.state.stationaryConfirmed or self.state.obdUnavailable) and self.rect[2] >= 100 and self.rect[3] >= 100
        request = (self.target, visible, self.rect)
        with self.lock:
            different = request != self.request
            if different:
                self.request = request
                self.revision += 1
        if not self.target and not self.stopping and not self.pending:
            self.timer.stop()
            return
        if not self.closed and (different or self.target):
            self.timer.start()
            self._tick()

    @Slot()
    def _tick(self):
        """同時実行を避けて最新要求を処理する。ブラウザを毎回起動し直さない。"""
        if self.pending or self.closed:
            return
        with self.lock:
            revision, request = self.revision, self.request
        self.pending = True

        def current():
            """ワーカー実行中の画面離脱や停止要求を配置前に再確認する。"""
            with self.lock:
                return not self.closed and revision == self.revision

        def work():
            """失敗時にはブラウザを閉じ、見えない画面や音声を残さない。"""
            try:
                message = self.browser.reconcile(request, current)
                return message, False
            except Exception as exc:
                self.browser.close()
                return str(exc) or "公式ブラウザを操作できませんでした", True

        def done(future):
            """結果をQtスレッドへ戻す。終了中はUIへ通知しない。"""
            try:
                message, error = future.result()
            except Exception:
                message, error = "公式ブラウザを終了できませんでした", True
            if not self.closed:
                self.completed.emit(revision, message, error)

        self.pool.submit(work).add_done_callback(done)

    @Slot(int, str, bool)
    def _complete(self, revision, message, error):
        """古い要求の結果で現在の音源表示を上書きしない。"""
        self.pending = False
        if self.closed:
            return
        if revision != self.revision:
            self._tick()
            return
        self.message = message
        if error:
            self.target = ""
            with self.lock:
                self.request = ("", False, self.rect)
                self.revision += 1
        if not self.target:
            self.stopping = False
            self.timer.stop()
            next_source = self.pending_open
            self.pending_open = ""
            if next_source == self.selected and self.state.screen == "audio":
                self.open()
        self.changed.emit()

    def close(self):
        """配置中の要求を無効化してから専用ブラウザを終了する。"""
        if self.closed:
            return
        with self.lock:
            self.closed = True
        self.timer.stop()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.browser.close()
