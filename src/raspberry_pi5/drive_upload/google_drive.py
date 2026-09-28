"""Google Drive v3の再開可能アップロード。秘密URIは例外やログへ出さない。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from drive_upload.config import UploadError
from drive_upload.network_policy import check_google_url

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FIELDS = "id,parents,trashed,size,md5Checksum,appProperties,version,mimeType,capabilities(canAddChildren)"


def save_credentials(path: Path, credentials) -> None:
    """更新トークンを含む認証ファイルを、所有者限定の権限で原子的に保存する。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".drive-token-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(credentials.to_json())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def authorized_session(credential_path: Path, before_request, timeout: float):
    """Google公式OAuth資格情報とrequestsを使う。HTTPの自動再送は行わない。"""
    from google.auth.exceptions import RefreshError, TransportError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    import requests

    try:
        credentials = Credentials.from_authorized_user_file(str(credential_path), SCOPES)
    except (OSError, ValueError) as exc:
        raise UploadError("AUTHENTICATION_REQUIRED") from exc

    class GuardedSession(requests.Session):
        def request(self, method, url, **kwargs):
            """GuardedSessionのrequestの内部処理を実行する。"""
            before_request(url)
            kwargs["timeout"] = timeout
            kwargs["allow_redirects"] = False
            return super().request(method, url, **kwargs)

    transport = GuardedSession()
    transport.trust_env = False

    class CredentialSession:
        def request(self, method, url, **kwargs):
            """CredentialSessionのrequestの内部処理を実行する。"""
            try:
                if not credentials.valid:
                    credentials.refresh(Request(session=transport))
                    save_credentials(credential_path, credentials)
                headers = dict(kwargs.pop("headers", {}))
                headers["Authorization"] = f"Bearer {credentials.token}"
                return transport.request(method, url, headers=headers, **kwargs)
            except RefreshError as exc:
                raise UploadError("AUTHENTICATION_REQUIRED") from exc
            except (TransportError, requests.RequestException) as exc:
                raise UploadError("NETWORK_ERROR", retryable=True) from exc

        def close(self):
            """保持中の資源を解放する。"""
            transport.close()

    return CredentialSession()


class GoogleDriveClient:
    """アップロードIDを先に発行し、受領済みバイトはサーバー応答だけで決定する。"""

    def __init__(self, session, timeout: float = 15) -> None:
        """設定と内部状態を初期化する。"""
        self.session, self.timeout = session, timeout

    def _request(self, method: str, url: str, **kwargs):
        """GoogleDriveClientの_requestの内部処理を実行する。"""
        check_google_url(url)
        response = self.session.request(method, url, timeout=self.timeout, allow_redirects=False, **kwargs)
        return response

    @staticmethod
    def _error(response) -> None:
        """GoogleDriveClientの_errorの内部処理を実行する。"""
        status = response.status_code
        reasons = set()
        try:
            reasons = {item.get("reason") for item in response.json().get("error", {}).get("errors", [])}
        except (ValueError, AttributeError, TypeError):
            pass
        try:
            delay = max(0.0, min(float(response.headers.get("Retry-After", "0")), 86400.0))
        except ValueError:
            delay = 0
        if status == 429 or status >= 500 or reasons & {"rateLimitExceeded", "userRateLimitExceeded"}:
            raise UploadError("DRIVE_RATE_LIMIT" if status < 500 else "DRIVE_TEMPORARY_ERROR", retryable=True, retry_after=delay)
        if status == 401:
            raise UploadError("AUTHENTICATION_REQUIRED")
        if "storageQuotaExceeded" in reasons:
            raise UploadError("DRIVE_STORAGE_FULL")
        if status == 409:
            raise UploadError("REMOTE_ID_CONFLICT", retryable=True)
        raise UploadError(f"DRIVE_HTTP_{status}")

    def _json(self, response) -> dict:
        """GoogleDriveClientの_jsonの内部処理を実行する。"""
        if response.status_code not in (200, 201):
            self._error(response)
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except ValueError as exc:
            raise UploadError("INVALID_DRIVE_RESPONSE", retryable=True) from exc

    def account_id(self) -> str:
        """GoogleDriveClientのaccount_idの内部処理を実行する。"""
        data = self._json(self._request("GET", f"{API}/about", params={"fields": "user(permissionId)"}))
        account = data.get("user", {}).get("permissionId")
        if not isinstance(account, str) or not account:
            raise UploadError("ACCOUNT_ID_MISSING")
        return account

    def check_destination(self, account: str, folder: str) -> None:
        """GoogleDriveClientのcheck_destinationの内部処理を実行する。"""
        if self.account_id() != account:
            raise UploadError("GOOGLE_ACCOUNT_CHANGED")
        data = self.metadata(folder)
        if (not data or data.get("trashed") is not False or data.get("mimeType") != "application/vnd.google-apps.folder"
                or data.get("capabilities", {}).get("canAddChildren") is not True):
            raise UploadError("DRIVE_FOLDER_UNAVAILABLE")

    def generate_id(self) -> str:
        """GoogleDriveClientのgenerate_idの内部処理を実行する。"""
        data = self._json(self._request("GET", f"{API}/files/generateIds", params={"count": 1, "space": "drive", "type": "files"}))
        ids = data.get("ids", [])
        if not ids or not isinstance(ids[0], str):
            raise UploadError("REMOTE_ID_MISSING", retryable=True)
        return ids[0]

    def create_folder(self, name: str) -> str:
        """初期設定でのみ専用フォルダを作成し、drive.fileスコープから利用する。"""
        planned_id = self.generate_id()
        result = self._json(self._request("POST", f"{API}/files", json={
            "id": planned_id, "name": name, "mimeType": "application/vnd.google-apps.folder"}, params={"fields": "id"}))
        return result["id"]

    def metadata(self, remote_id: str) -> dict | None:
        """GoogleDriveClientのmetadataの内部処理を実行する。"""
        if not re.fullmatch(r"[A-Za-z0-9_-]+", remote_id):
            raise UploadError("INVALID_REMOTE_ID")
        response = self._request("GET", f"{API}/files/{remote_id}", params={"fields": FIELDS})
        return None if response.status_code == 404 else self._json(response)

    def start_upload(self, row: dict, folder: str) -> str:
        """GoogleDriveClientのstart_uploadの内部処理を実行する。"""
        mime = "video/mp4" if row["relative_path"].lower().endswith(".mp4") else "video/x-matroska"
        response = self._request("POST", UPLOAD, params={"uploadType": "resumable", "fields": FIELDS},
            headers={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(row["size_bytes"])},
            json={"id": row["planned_remote_id"], "name": f'{row["file_id"]}-{Path(row["relative_path"]).name}',
                  "parents": [folder], "mimeType": mime, "appProperties": {"file_id": row["file_id"], "camera_id": row["camera_id"]}})
        if response.status_code not in (200, 201):
            self._error(response)
        uri = response.headers.get("Location", "")
        check_google_url(uri)
        return uri

    def _progress(self, response, total: int) -> tuple[bool, int]:
        """308のRangeがなければ0バイト。308は転送完了にしない。"""
        if response.status_code in (200, 201):
            return True, total
        if response.status_code in (404, 410):
            raise UploadError("UPLOAD_SESSION_EXPIRED", retryable=True)
        if response.status_code != 308:
            self._error(response)
        raw = response.headers.get("Range")
        if raw is None:
            return False, 0
        match = re.fullmatch(r"bytes=0-(\d+)", raw)
        if not match or not 0 < int(match[1]) + 1 <= total:
            raise UploadError("INVALID_DRIVE_RANGE")
        return False, int(match[1]) + 1

    def resume_upload(self, uri: str, total: int) -> tuple[bool, int]:
        """GoogleDriveClientのresume_uploadの内部処理を実行する。"""
        response = self._request("PUT", uri, data=b"", headers={"Content-Length": "0", "Content-Range": f"bytes */{total}"})
        return self._progress(response, total)

    def send_chunk(self, uri: str, data: bytes, offset: int, total: int) -> tuple[bool, int]:
        """GoogleDriveClientのsend_chunkの内部処理を実行する。"""
        response = self._request("PUT", uri, data=data, headers={"Content-Length": str(len(data)),
            "Content-Type": "application/octet-stream", "Content-Range": f"bytes {offset}-{offset + len(data) - 1}/{total}"})
        return self._progress(response, total)

    def close(self) -> None:
        """保持中の資源を解放する。"""
        self.session.close()
