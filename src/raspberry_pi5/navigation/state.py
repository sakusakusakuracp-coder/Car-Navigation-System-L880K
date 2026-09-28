"""ナビ管理ソフトが保持する状態と処理結果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Phase(str, Enum):
    STOPPED = "STOPPED"
    CHECKING = "CHECKING"
    STARTING_CONTAINER = "STARTING_CONTAINER"
    STARTING_SESSION = "STARTING_SESSION"
    STARTING_APP = "STARTING_APP"
    READY = "READY"
    READY_WITHOUT_FIX = "READY_WITHOUT_FIX"
    RECOVERING = "RECOVERING"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass
class OperationResult:
    success: bool
    state: str
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class NavigationState:
    phase: Phase = Phase.STOPPED
    container_state: str = "UNKNOWN"
    session_state: str = "UNKNOWN"
    android_state: str = "UNKNOWN"
    app_state: dict[str, str] = field(default_factory=dict)
    location_state: dict[str, Any] = field(default_factory=lambda: {"state": "UNKNOWN"})
    requested_visibility: str = "hidden"
    actual_visibility: str = "unknown"
    stop_result: dict[str, Any] = field(default_factory=dict)
    operation_epoch: int = 0
    failure_reason: str = ""
    last_result: str = ""

    def as_dict(self) -> dict[str, Any]:
        """外部へ通知できる、推測を含まない状態を返す。"""
        return {
            "phase": self.phase.value,
            "container_state": self.container_state,
            "session_state": self.session_state,
            "android_state": self.android_state,
            "app_state": dict(self.app_state),
            "location_state": dict(self.location_state),
            "requested_visibility": self.requested_visibility,
            "actual_visibility": self.actual_visibility,
            "stop_result": dict(self.stop_result),
            "operation_epoch": self.operation_epoch,
            "failure_reason": self.failure_reason,
            "last_result": self.last_result,
        }
