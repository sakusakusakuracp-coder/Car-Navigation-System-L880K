"""エラーの発生元・操作・原因が利用者へ伝わることを確認する。"""

import unittest

from ui.controllers.error_messages import command_failure, task_failure


class ErrorMessagesTest(unittest.TestCase):
    def test_same_reason_is_distinguished_by_service_and_action(self):
        """同じ通信エラーでも、どちらのナビの何が失敗したか識別できる。"""
        for service, action, title in [
            ("11 LIVI連携", "show_livi", "LIVIの画面表示"),
            ("02 Waydroidナビ管理", "prelaunch_navigation", "OsmAnd（Waydroid）の先行起動"),
            ("09 OBD2車両情報取得", "set_polling_mode", "OBD2車両情報の取得項目の切替"),
        ]:
            with self.subTest(service=service):
                message = command_failure({"service": service, "action": action, "reason": "通信が切断されました"})
                self.assertEqual(message, title + "に失敗しました。\n原因: 通信が切断されました")

    def test_unknown_identifiers_and_multiline_details_are_preserved(self):
        """未登録サービスや長い複数行の原因も捨てずに履歴へ渡す。"""
        reason = "詳細情報" * 100 + "\nsecond line"
        message = command_failure({"service": "custom_service", "operation": "custom_operation", "reason": reason})
        self.assertIn("custom_serviceのcustom_operation", message)
        self.assertTrue(message.endswith(reason))

    def test_waydroid_home_error_is_not_labeled_as_osmand_error(self):
        """Androidホームの失敗をOsmAnd画面の失敗と誤表示しない。"""
        message = command_failure(
            {
                "service": "02 Waydroidナビ管理",
                "action": "show_waydroid_home",
                "reason": "AndroidホームウィンドウをSwayから確認できませんでした",
            }
        )

        self.assertEqual(
            message,
            "WaydroidのAndroidアプリ画面表示に失敗しました。\n"
            "原因: AndroidホームウィンドウをSwayから確認できませんでした",
        )

    def test_missing_reason_is_not_an_invented_cause(self):
        """理由が欠けた応答を、特定の機器故障と断定しない。"""
        self.assertIn("詳しい理由が届いていません", command_failure({}))

    def test_background_operations_are_named(self):
        """録画や音楽の失敗が単なる『失敗』という表示にならない。"""
        self.assertIn("録画設定の適用に失敗", task_failure("camera_apply", "timeout"))
        self.assertIn("音楽の再生操作に失敗", task_failure("audio_command", "timeout"))
