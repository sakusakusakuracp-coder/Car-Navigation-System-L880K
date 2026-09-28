"""サブコントローラ1の状態・映像JSON Lines配信。"""

from __future__ import annotations

import uuid
from typing import Any

from subcontroller_common import JsonLinePublisher, make_event


class Sub1Api:
    def __init__(self, publisher: JsonLinePublisher) -> None:
        """設定と内部状態を初期化する。"""
        self.publisher = publisher
        self.boot_id = f"sub1-{uuid.uuid4().hex}"
        self.sequence = 0

    def publish_state(self, reverse: dict[str, Any], camera_state: dict[str, Any], service_state: str) -> bool:
        """Sub1Apiのpublish_stateの内部処理を実行する。"""
        self.sequence += 1
        return self.publisher.publish(make_event("15 サブコントローラ1後方系", self.boot_id, self.sequence, "sub1.status", service_state=service_state, reverse_state=reverse, camera_state=camera_state, peer_generation=self.publisher.connection_generation))

    def publish_frame(self, frame: dict[str, Any]) -> bool:
        """Sub1Apiのpublish_frameの内部処理を実行する。"""
        self.sequence += 1
        return self.publisher.publish(make_event("15 サブコントローラ1後方系", self.boot_id, self.sequence, "camera.frame", **frame))
