"""ドアロック保護・駆動回路への出力ドライバ。"""

from __future__ import annotations

import logging
import time

LOGGER = logging.getLogger("l880k.door-lock")


class OutputDriver:
    def release_all(self) -> None:
        """すべての施錠・解錠出力を非駆動状態へ戻す。"""
        raise NotImplementedError

    def activate(self, direction: str) -> None:
        """指定方向の出力を駆動する。"""
        raise NotImplementedError

    def close(self) -> None:
        """出力を解除してドライバ資源を解放する。"""
        self.release_all()


class DryRunOutputDriver(OutputDriver):
    """出力を記録するだけの代替ドライバ。"""

    def __init__(self) -> None:
        """実機を駆動せず操作履歴を記録する。"""
        self.active_direction: str | None = None
        self.history: list[tuple[str, float]] = []

    def release_all(self) -> None:
        """モック出力を非駆動として履歴へ記録する。"""
        self.active_direction = None
        self.history.append(("RELEASE", time.monotonic()))

    def activate(self, direction: str) -> None:
        """モックへ施錠・解錠操作を記録する。"""
        if self.active_direction is not None:
            raise RuntimeError("同時出力を禁止しています")
        self.active_direction = direction
        self.history.append((direction, time.monotonic()))


class LgpioOutputDriver(OutputDriver):
    """Pi 5のlgpioを利用した2系統出力。車体12Vは回路側で駆動する。"""

    def __init__(self, lock_gpio: int, unlock_gpio: int, active_level: int, release_level: int) -> None:
        """施錠・解錠GPIOを初期化し、非駆動レベルを設定する。"""
        try:
            import lgpio
        except ImportError as exc:
            raise RuntimeError("実機制御にはlgpioが必要です") from exc
        if lock_gpio == unlock_gpio:
            raise ValueError("LOCKとUNLOCKのGPIOは別にしてください")
        self._lgpio = lgpio
        self._chip = lgpio.gpiochip_open(0)
        self._pins = {"LOCK": lock_gpio, "UNLOCK": unlock_gpio}
        self._active = active_level
        self._release = release_level
        for gpio in self._pins.values():
            lgpio.gpio_claim_output(self._chip, gpio, release_level)

    def release_all(self) -> None:
        """2系統のGPIOをともに非駆動レベルへ戻す。"""
        errors: list[Exception] = []
        for gpio in self._pins.values():
            try:
                self._lgpio.gpio_write(self._chip, gpio, self._release)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        if errors:
            raise RuntimeError(f"出力解除に失敗しました: {errors[0]}")

    def activate(self, direction: str) -> None:
        """指定方向のGPIOだけを有効レベルへ切り替える。"""
        self.release_all()
        self._lgpio.gpio_write(self._chip, self._pins[direction], self._active)

    def close(self) -> None:
        """GPIO出力を解除し、GPIOチップを閉じる。"""
        try:
            self.release_all()
            self._lgpio.gpiochip_close(self._chip)
        except Exception:  # noqa: BLE001
            LOGGER.exception("ドアロックGPIOの終了に失敗しました")
