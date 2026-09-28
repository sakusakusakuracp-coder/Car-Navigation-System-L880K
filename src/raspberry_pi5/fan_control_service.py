"""Pi 5の4ピンPWM冷却ファンを制御する常駐サービス。

1台のPWM入力とTACH入力を制御する。
実機ではLinuxのPWMデバイスで25kHzのPWMを出力し、TACH入力だけにlgpioを使う。
開発環境では``--dry-run``でGPIOへ触れずに制御則を検証できる。
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Any

from telemetry_ipc import JsonLinesEventServer, runtime_socket_path

LOGGER = logging.getLogger("l880k.fan")

FOUR_WIRE_PWM_MIN_HZ = 21_000
FOUR_WIRE_PWM_MAX_HZ = 28_000


@dataclass(frozen=True)
class FanConfig:
    enabled: bool = False
    pwm_gpio: int = 18
    tach_gpio: int = 20
    pwm_frequency_hz: int = 25_000
    pwm_backend: str = "kernel"
    pwm_chip_path: str = "/sys/class/pwm/pwmchip0"
    pwm_channel: int = 0
    pulses_per_revolution: int = 2
    control_period_ms: int = 1_000
    tach_window_ms: int = 1_000
    start_duty: float = 0.35
    low_temperature_c: float = 55.0
    high_temperature_c: float = 70.0
    fail_safe_temperature_c: float = 85.0
    low_duty: float = 0.25
    high_duty: float = 0.75
    max_duty: float = 1.0
    tach_min_rpm: float = 250.0
    tach_grace_ms: int = 3_000
    temperature_path: str = "/sys/class/thermal/thermal_zone0/temp"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "FanConfig":
        """設定辞書を型付きファン設定へ変換し、周波数と安全条件を検証する。"""
        values = {field: data[field] for field in cls.__dataclass_fields__ if field in data}
        config = cls(**values)
        if config.pwm_gpio == config.tach_gpio:
            raise ValueError("PWM GPIOとTACH GPIOは同じにできません")
        if not 0 < config.pulses_per_revolution <= 20:
            raise ValueError("pulses_per_revolutionが不正です")
        if not FOUR_WIRE_PWM_MIN_HZ <= config.pwm_frequency_hz <= FOUR_WIRE_PWM_MAX_HZ:
            raise ValueError("4ピンPWMファンの周波数は21kHzから28kHzの範囲が必要です")
        if config.pwm_backend not in {"kernel"}:
            raise ValueError("pwm_backendはkernelを指定してください")
        if not config.pwm_chip_path or config.pwm_channel < 0:
            raise ValueError("PWMデバイスのパスとチャンネルが不正です")
        if config.control_period_ms <= 0:
            raise ValueError("制御周期は正数が必要です")
        for name in ("start_duty", "low_duty", "high_duty", "max_duty"):
            value = float(getattr(config, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name}は0から1の範囲が必要です")
        if not config.low_duty <= config.high_duty <= config.max_duty:
            raise ValueError("温度帯のデューティ順序が不正です")
        return config


class FanHardware:
    """1台分のPWMとTACHの実装境界。"""

    def apply_duty(self, duty: float) -> None:
        """1台のPWMファンへデューティを適用する。"""
        raise NotImplementedError

    def read_rpm(self, window_s: float, pulses_per_revolution: int) -> float | None:
        """冷却ファンのTACHパルスから回転数を読み取る。"""
        raise NotImplementedError

    def close(self) -> None:
        """ファン制御用資源を解放する。"""
        pass


class DryRunFanHardware(FanHardware):
    """GPIOへ触れず、制御則の試験用に動作を模擬する。"""

    def __init__(self) -> None:
        """実GPIOを触らないテスト用のデューティ状態を初期化する。"""
        self.duty = 0.0

    def apply_duty(self, duty: float) -> None:
        """テスト用にPWMデューティだけを記録する。"""
        self.duty = duty

    def read_rpm(self, window_s: float, pulses_per_revolution: int) -> float | None:
        """テスト用の仮想回転数をデューティから返す。"""
        if self.duty <= 0.0:
            return 0.0
        return 600.0 + self.duty * 3_600.0


class KernelPwmOutput:
    """Linux PWM sysfsで4ピンファン用のハードウェアPWMを出力する。"""

    def __init__(self, config: FanConfig) -> None:
        """Linux PWM sysfsチャンネルを確保し、指定周波数で初期化する。"""
        self._chip_path = Path(config.pwm_chip_path)
        self._channel_path = self._chip_path / f"pwm{config.pwm_channel}"
        self._channel = config.pwm_channel
        self._exported = False
        self._period_ns = round(1_000_000_000 / config.pwm_frequency_hz)

        if not self._channel_path.exists():
            try:
                (self._chip_path / "export").write_text(str(self._channel), encoding="ascii")
                self._exported = True
            except OSError as exc:
                raise RuntimeError(
                    f"PWMチャンネルをexportできません: {self._chip_path} channel={self._channel}"
                ) from exc

        if not self._channel_path.exists():
            if self._exported:
                try:
                    (self._chip_path / "unexport").write_text(str(self._channel), encoding="ascii")
                except OSError:
                    LOGGER.exception("初期化失敗後のPWMチャンネル解放に失敗しました")
            raise RuntimeError(f"PWMチャンネルが見つかりません: {self._channel_path}")

        try:
            self._write("enable", "0")
            self._write("period", str(self._period_ns))
            self._write("duty_cycle", "0")
        except OSError as exc:
            self.close()
            raise RuntimeError(
                f"PWMデバイスを初期化できません: {self._channel_path}"
            ) from exc

    def _write(self, name: str, value: str) -> None:
        """PWM sysfsの指定属性へASCII値を書き込む。"""
        (self._channel_path / name).write_text(value, encoding="ascii")

    def apply_duty(self, duty: float) -> None:
        """指定デューティを周期内の有効時間へ変換して出力する。"""
        duty = min(1.0, max(0.0, duty))
        duty_ns = min(self._period_ns, round(self._period_ns * duty))
        self._write("duty_cycle", str(duty_ns))
        self._write("enable", "1")

    def close(self) -> None:
        """PWMを停止し、サービスがexportしたチャンネルだけを解放する。"""
        try:
            if self._channel_path.exists():
                self._write("duty_cycle", "0")
                self._write("enable", "0")
        finally:
            if self._exported and (self._chip_path / "unexport").exists():
                try:
                    (self._chip_path / "unexport").write_text(str(self._channel), encoding="ascii")
                except OSError:
                    LOGGER.exception("PWMチャンネルのunexportに失敗しました")


class LgpioFanHardware(FanHardware):
    """PWMはLinux PWM、TACHエッジ取得はlgpioで行う実装。"""

    def __init__(self, config: FanConfig) -> None:
        """Linux PWMと冷却ファンのTACH入力をlgpioへ接続する。"""
        try:
            import lgpio
        except ImportError as exc:
            raise RuntimeError("実機制御にはlgpioが必要です。開発時は--dry-runを使ってください") from exc
        self._lgpio = lgpio
        self._config = config
        self._pwm = KernelPwmOutput(config)
        try:
            self._chip = lgpio.gpiochip_open(0)
            lgpio.gpio_claim_input(self._chip, config.tach_gpio)
        except Exception:
            self._pwm.close()
            raise
        self._edges: deque[int] = deque(maxlen=4096)
        self._lock = Lock()
        self._callback = lgpio.callback(self._chip, config.tach_gpio, lgpio.RISING_EDGE, self._on_edge)

    def _on_edge(self, _chip: int, _gpio: int, _level: int, _tick: int) -> None:
        """冷却ファンのTACH立上り時刻を回転数計算用バッファへ保存する。"""
        with self._lock:
            # lgpioのtickとPythonの単調時計を混在させない。
            self._edges.append(int(time.monotonic() * 1_000_000))

    def apply_duty(self, duty: float) -> None:
        """1台の冷却ファンのPWM出力へデューティを渡す。"""
        self._pwm.apply_duty(duty)

    def read_rpm(self, window_s: float, pulses_per_revolution: int) -> float | None:
        """指定窓内のTACHパルス数から冷却ファンのRPMを計算する。"""
        cutoff_us = int(time.monotonic() * 1_000_000 - window_s * 1_000_000)
        with self._lock:
            while self._edges and self._edges[0] < cutoff_us:
                self._edges.popleft()
            count = len(self._edges)
        if count == 0:
            return 0.0
        return count / pulses_per_revolution / window_s * 60.0

    def close(self) -> None:
        """PWMとGPIOチップを安全に閉じる。"""
        try:
            self._pwm.close()
            self._lgpio.gpiochip_close(self._chip)
        except Exception:  # noqa: BLE001 - 終了処理では元の例外を隠さない
            LOGGER.exception("PWM/GPIOの終了に失敗しました")


def read_temperature(path: str) -> float | None:
    """thermal zoneのミリ度Cを読み、異常値は無効にする。"""
    try:
        value = float(Path(path).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    temperature = value / 1000.0
    return temperature if -40.0 <= temperature <= 150.0 else None


class FanController:
    """温度表、TACH監視、異常時最大冷却を一つの周期で実行する。"""

    def __init__(self, config: FanConfig, hardware: FanHardware, server: JsonLinesEventServer | None = None) -> None:
        """温度制御、PWM/TACH境界、イベント配信、停止状態を初期化する。"""
        self.config = config
        self.hardware = hardware
        self.server = server
        self.stop_event = Event()
        self.boot_id = f"fan-{time.time_ns()}"
        self.sequence = 0
        self.last_duty = 0.0
        self.started_at = time.monotonic()

    def calculate_duty(self, temperature: float | None, rpm: float | None) -> tuple[float, str]:
        """温度と回転状態から1台の冷却ファンへのPWM指令と理由を決める。"""
        if temperature is None:
            return self.config.max_duty, "TEMPERATURE_UNKNOWN"
        if temperature >= self.config.fail_safe_temperature_c:
            return self.config.max_duty, "OVER_TEMPERATURE"
        if self.last_duty > 0.0 and rpm is not None and rpm < self.config.tach_min_rpm:
            if (time.monotonic() - self.started_at) * 1000 >= self.config.tach_grace_ms:
                return self.config.max_duty, "TACH_LOW"
        if temperature >= self.config.high_temperature_c:
            return self.config.high_duty, "HIGH_TEMPERATURE"
        if temperature >= self.config.low_temperature_c:
            span = self.config.high_temperature_c - self.config.low_temperature_c
            ratio = (temperature - self.config.low_temperature_c) / max(span, 0.1)
            return min(self.config.max_duty, self.config.low_duty + ratio * (self.config.high_duty - self.config.low_duty)), "TEMPERATURE_TABLE"
        return 0.0, "COOL"

    def publish(self, *, temperature: float | None, rpm: float | None, duty: float, reason: str, status: str) -> dict[str, Any]:
        """ファン制御結果を周波数、デューティ、TACH状態付きで通知する。"""
        self.sequence += 1
        event = {
            "schema_version": "1.0",
            "event": "fan.status",
            "source": "10 冷却ファン制御",
            "boot_id": self.boot_id,
            "sequence": self.sequence,
            "observed_at_monotonic_ms": int(time.monotonic() * 1000),
            "status": status,
            "temperature_c": temperature,
            "fan_rpm": rpm,
            "pwm_frequency_hz": self.config.pwm_frequency_hz,
            "pwm_backend": self.config.pwm_backend,
            "pwm_duty": duty,
            "reason": reason,
        }
        if self.server:
            self.server.publish(event)
        return event

    def tick(self) -> dict[str, Any]:
        """温度とRPMを1回読み、デューティ決定、出力、状態通知を行う。"""
        temperature = read_temperature(self.config.temperature_path)
        rpm = self.hardware.read_rpm(self.config.tach_window_ms / 1000.0, self.config.pulses_per_revolution)
        duty, reason = self.calculate_duty(temperature, rpm)
        self.hardware.apply_duty(duty)
        self.last_duty = duty
        status = "FAILSAFE" if reason in {"TEMPERATURE_UNKNOWN", "OVER_TEMPERATURE", "TACH_LOW"} else "RUNNING"
        return self.publish(temperature=temperature, rpm=rpm, duty=duty, reason=reason, status=status)

    def run(self, *, once: bool = False) -> int:
        """設定された周期で冷却制御を継続し、終了時に出力を停止する。"""
        if not self.config.enabled:
            self.publish(temperature=None, rpm=None, duty=0.0, reason="DISABLED", status="DISABLED")
            return 0
        try:
            self.hardware.apply_duty(0.0)
            self.started_at = time.monotonic()
            while not self.stop_event.is_set():
                event = self.tick()
                LOGGER.info("fan status: %s", json.dumps(event, ensure_ascii=False))
                if once:
                    break
                self.stop_event.wait(self.config.control_period_ms / 1000.0)
            return 0
        finally:
            self.hardware.close()
            if self.server:
                self.server.stop()

    def request_stop(self) -> None:
        """冷却制御ループへ停止要求を通知する。"""
        self.stop_event.set()


def load_config(path: str) -> FanConfig:
    """JSON設定ファイルを読み、検証済みのファン設定を返す。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return FanConfig.from_mapping(data)


def main(argv: list[str] | None = None) -> int:
    """PWMファンサービスを実機またはdry-runで起動する。"""
    parser = argparse.ArgumentParser(description="L880K PWM fan control service")
    parser.add_argument("--config", required=True)
    parser.add_argument("--socket", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = load_config(args.config)
        hardware = DryRunFanHardware() if args.dry_run else LgpioFanHardware(config)
        server = None if args.dry_run else JsonLinesEventServer(args.socket or runtime_socket_path("l880k-fan.sock"))
        if server:
            server.start()
        controller = FanController(config, hardware, server)
        signal.signal(signal.SIGTERM, lambda _signum, _frame: controller.request_stop())
        signal.signal(signal.SIGINT, lambda _signum, _frame: controller.request_stop())
        return controller.run(once=args.once)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.error("冷却ファン制御を開始できません: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
