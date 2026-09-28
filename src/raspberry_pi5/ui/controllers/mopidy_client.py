"""Mopidyの公式HTTP JSON-RPCを使い、実再生状態と操作結果を取得する。"""

import json
import math
import threading
from copy import deepcopy
from urllib.request import Request, urlopen


class MopidyClient:
    def __init__(self, url="http://127.0.0.1:6680/mopidy/rpc"):
        """タイムアウト付きの接続先と要求番号を保持する。"""
        self.url = url
        self.sequence = 0
        self.lock = threading.RLock()

    def call(self, method, params=None):
        """応答IDとエラーを確認し、受付だけを成功表示しない。"""
        with self.lock:
            self.sequence += 1
            request_id = self.sequence
            data = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}).encode()
            request = Request(self.url, data=data, headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=2) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("音楽一覧が大きすぎます")
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get("id") != request_id or result.get("error") or "result" not in result:
                raise ValueError("Mopidyが要求を処理できませんでした")
            return result["result"]

    def status(self):
        """プレイヤーの状態を取得する。曲名・再生時間を画面内で捏造しない。"""
        with self.lock:
            state = self.call("core.playback.get_state")
            track = self.call("core.playback.get_current_track") or {}
            return {"connected": True, "state": state, "title": track.get("name") or "未選曲",
                    "artist": " / ".join(x.get("name", "") for x in track.get("artists", [])),
                    "length": track.get("length") or 0, "position": self.call("core.playback.get_time_position") or 0,
                    "volume": self.call("core.mixer.get_volume"),
                    "tracks": self.call("core.tracklist.get_tl_tracks") or []}

    def command(self, action, value=None):
        """許可した音楽操作だけを送信する。未確認の操作を再接続後に再送しない。"""
        with self.lock:
            if action in {"play", "pause", "next", "previous", "stop"}:
                result = self.call("core.playback." + action)
            elif action == "volume":
                if type(value) not in (int, float) or not 0 <= value <= 100:
                    raise ValueError("音量は0から100です")
                result = self.call("core.mixer.set_volume", {"volume": round(value)})
            elif action == "seek":
                if type(value) not in (int, float) or not 0 <= value < 86400000:
                    raise ValueError("再生位置が不正です")
                result = self.call("core.playback.seek", {"time_position": round(value)})
            elif action == "select":
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or value != int(value):
                    raise ValueError("曲の番号が不正です")
                result = self.call("core.playback.play", {"tlid": int(value)})
            elif action == "add":
                if not isinstance(value, str) or not value or len(value) > 4096:
                    raise ValueError("曲のURIが不正です")
                result = self.call("core.tracklist.add", {"uris": [value]})
            elif action == "add_playlist":
                playlist = self.playlist(value)
                uris = [x["uri"] for x in playlist.get("tracks", []) if x.get("uri")]
                if not uris:
                    raise ValueError("プレイリストに再生可能な曲がありません")
                result = self.call("core.tracklist.add", {"uris": uris})
            else:
                raise ValueError("未対応の音楽操作です")
            if result is False or (action in {"add", "add_playlist"} and not result):
                raise ValueError("音楽操作が拒否されました")
            return self.status()

    def browse(self, uri=None):
        """Mopidyで有効なライブラリのフォルダと曲を一覧にする。"""
        result = self.call("core.library.browse", {"uri": uri or None}) or []
        return result

    def search(self, text):
        """全ライブラリを検索し、重複URIを除いて曲の選択用一覧へ変換する。"""
        text = text.strip()
        if not text or len(text) > 200:
            raise ValueError("検索語は1から200文字で指定してください")
        results = self.call("core.library.search", {"query": {"any": [text]}, "exact": False}) or []
        tracks = {}
        for result in results:
            for track in result.get("tracks", []):
                if track.get("uri"):
                    tracks.setdefault(track["uri"], {"type": "track", "uri": track["uri"], "name": track.get("name") or track["uri"]})
        return list(tracks.values())

    def playlists(self):
        """バックエンドが公開している保存済みプレイリストを取得する。"""
        return self.call("core.playlists.as_list") or []

    def playlist(self, uri):
        """編集対象を読み込み、存在しない場合は空の編集データを捏造しない。"""
        result = self.call("core.playlists.lookup", {"uri": uri})
        if not result:
            raise ValueError("プレイリストが見つかりません")
        return result

    def save_playlist(self, original, edited):
        """外部変更を検出してから保存し、サーバーが返した確定内容を使用する。"""
        if not isinstance(edited.get("name"), str) or not edited["name"].strip() or len(edited["name"]) > 200:
            raise ValueError("プレイリスト名は1から200文字で指定してください")
        with self.lock:
            if self.playlist(original["uri"]) != original:
                raise ValueError("プレイリストが別の処理で変更されました。再取得してください")
            result = self.call("core.playlists.save", {"playlist": edited})
            if not result:
                raise ValueError("プレイリストを保存できません。バックエンドの書込対応と権限を確認してください")
            return result

    def create_playlist(self, name):
        """利用可能な既定バックエンドに空のプレイリストを作る。曲は別途追加する。"""
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("プレイリスト名は1から200文字で指定してください")
        result = self.call("core.playlists.create", {"name": name.strip()})
        if not result:
            raise ValueError("プレイリストを作成できません。書込対応バックエンドが必要です")
        return result

    def delete_playlist(self, original):
        """確認時から外部変更がないプレイリストだけを削除する。曲ファイルは削除しない。"""
        with self.lock:
            if self.playlist(original["uri"]) != original:
                raise ValueError("プレイリストが変更されました。再取得してください")
            if self.call("core.playlists.delete", {"uri": original["uri"]}) is not True:
                raise ValueError("プレイリストを削除できませんでした")
        return None

    def queue_tracks(self):
        """現在の再生キューから保存用Trackの独立したコピーを取得する。"""
        return [deepcopy(x["track"]) for x in (self.call("core.tracklist.get_tl_tracks") or [])]
