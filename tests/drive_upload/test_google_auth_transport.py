"""実認証ライブラリを使い、HTTPの出口だけを差し替えて秘密値と更新経路を検査する。"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

try:
    import requests
    from google.oauth2.credentials import Credentials
    DEPENDENCIES_AVAILABLE = True
except ImportError:
    DEPENDENCIES_AVAILABLE = False

from drive_upload.config import Paused
from drive_upload.google_drive import SCOPES, GoogleDriveClient, authorized_session, save_credentials


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, "requirements-drive.txtのライブラリが必要")
class AuthenticationTransportTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "token.json"
        # このテスト専用のダミー値。Googleへ接続する資格情報ではない。
        credentials = Credentials(token="test-expired", refresh_token="test-refresh",
                                  token_uri="https://oauth2.googleapis.com/token", client_id="test-client",
                                  client_secret="test-secret", scopes=SCOPES,
                                  expiry=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1))
        save_credentials(self.path, credentials)

    def test_refresh_and_api_both_pass_policy_without_redirects(self):
        checked, sent = [], []

        def fake_send(transport, request, **kwargs):
            sent.append((request, kwargs, transport.trust_env))
            response = requests.Response()
            response.status_code = 200
            response.request = request
            response.url = request.url
            data = ({"access_token": "test-new", "expires_in": 3600, "token_type": "Bearer"}
                    if "oauth2.googleapis.com" in request.url else {"user": {"permissionId": "test-account"}})
            response._content = json.dumps(data).encode()
            return response

        client = GoogleDriveClient(authorized_session(self.path, checked.append, 3))
        self.addCleanup(client.close)
        with patch.object(requests.Session, "send", fake_send):
            self.assertEqual(client.account_id(), "test-account")
        self.assertEqual(len(sent), 2)
        self.assertIn("oauth2.googleapis.com", checked[0])
        self.assertIn("www.googleapis.com", checked[1])
        self.assertEqual(sent[1][0].headers["Authorization"], "Bearer test-new")
        for request, kwargs, trust_env in sent:
            self.assertEqual(kwargs["timeout"], 3)
            self.assertFalse(kwargs["allow_redirects"])
            self.assertFalse(trust_env)
        self.assertEqual(json.loads(self.path.read_text())["token"], "test-new")

    def test_pause_prevents_even_token_refresh(self):
        def deny(_url):
            raise Paused("NETWORK_NOT_ALLOWED")

        client = GoogleDriveClient(authorized_session(self.path, deny, 3))
        self.addCleanup(client.close)
        with patch.object(requests.Session, "send") as send, self.assertRaises(Paused):
            client.account_id()
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
