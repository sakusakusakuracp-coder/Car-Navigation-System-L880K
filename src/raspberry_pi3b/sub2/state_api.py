"""サブコントローラ2の状態・映像JSON Lines配信。"""

from __future__ import annotations

import uuid
from typing import Any

from subcontroller_common import JsonLinePublisher, make_event


class Sub2Api:
    def __init__(self, publisher: JsonLinePublisher) -> None:
        """設定と内部状態を初期化する。"""
        self.publisher = publisher
        self.boot_id = f"sub2-{uuid.uuid4().hex}"
        self.sequence = 0

    def publish_state(self, left: dict[str, Any], right: dict[str, Any], camera_state: dict[str, Any], service_state: str) -> bool:
        """Sub2Apiのpublish_stateの内部処理を実行する。"""
        self.sequence += 1
        both = None if left.get("activity") is None or right.get("activity") is None else (left.get("activity") == "ACTIVE" and right.get("activity") == "ACTIVE")
        return self.publisher.publish(make_event("16 サブコントローラ2前方系", self.boot_id, self.sequence, "sub2.status", service_state=service_state, left_turn=left, right_turn=right, both_activity=both, camera_state=camera_state, peer_generation=self.publisher.connection_generation))

    def publish_frame(self, frame: dict[str, Any]) -> bool:
        """Sub2Apiのpublish_frameの内部処理を実行する。"""
        self.sequence += 1
        return self.publisher.publish(make_event("16 サブコントローラ2前方系", self.boot_id, self.sequence, "camera.frame", **frame))
