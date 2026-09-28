"""ACCとPi 5の通知を監視するMicroPython用電源管理。

GP10のHIGHは通知サービス稼働、LOWは状態不明であり、OS停止の証明ではない。
停止が確認できない間はJ2を再操作しない。Pi 5への5V給電は常時維持する。
"""
from machine import Pin
import time

ACC_INPUT_GPIO = 17
PI_J2_BUTTON_CTRL_GPIO = 14
PI_STATUS_INPUT_GPIO = 10
ONBOARD_LED_GPIO = 25

# 既存のHIGH動作リレー用。HW図の吸込み方式ならopen_drain_lowに変更する。
J2_CONTROL_MODE = "active_high"
PULSE_MS = 300
START_PULSE_COUNT = 1
SHUTDOWN_PULSE_COUNT = 2
SHUTDOWN_PULSE_GAP_MS = 500
DEBOUNCE_MS = 100
POLL_MS = 50
BOOT_TIMEOUT_MS = 90_000
SHUTDOWN_TIMEOUT_MS = 90_000
READY_STABLE_MS = 2_000
LED_BLINK_INTERVAL_MS = 500
LED_FAST_BLINK_INTERVAL_MS = 150
STATE_LOG_INTERVAL_MS = 2_000


def ticks_ms():
    """MicroPythonのミリ秒カウンタを取得する。"""
    return time.ticks_ms()


def elapsed_ms(start_ms):
    """カウンタの折返しを考慮した経過時間を取得する。"""
    return time.ticks_diff(ticks_ms(), start_ms)


class J2ButtonPulse:
    """J2に直結せず、外部の接点模擬回路を短時間だけ動作させる。"""

    def __init__(self, gpio, mode=J2_CONTROL_MODE):
        """出力開始と同時に非動作レベルを設定し、起動時の誤押下を防ぐ。"""
        if mode == "active_high":
            self.active, self.released = 1, 0
            pin_mode = Pin.OUT
        elif mode == "open_drain_low":
            self.active, self.released = 0, 1
            pin_mode = Pin.OPEN_DRAIN
        else:
            raise ValueError("J2_CONTROL_MODEが不正です")
        self.pin = Pin(gpio, pin_mode, value=self.released)

    def release(self):
        """HIGH動作回路ではLOW、吸込み回路ではHi-Zにして接点を開放する。"""
        self.pin.value(self.released)

    def pulse(self, pulse_ms=PULSE_MS):
        """例外やCtrl+Cの場合も接点を開放し、押しっぱなしを避ける。"""
        try:
            self.pin.value(self.active)
            time.sleep_ms(pulse_ms)
        finally:
            self.release()


class DebouncedInput:
    """初回を含め、100ms安定した値だけを採用する。未確定はNone。"""

    def __init__(self, gpio, pull=None):
        """入力を準備するが、起動直後の生値では操作を許可しない。"""
        self.pin = Pin(gpio, Pin.IN, pull) if pull is not None else Pin(gpio, Pin.IN)
        self._stable = None
        self._last_raw = self.pin.value()
        self._changed_at = ticks_ms()

    def value(self):
        """変化した候補値が一定時間継続したら安定値を更新する。"""
        raw = self.pin.value()
        if raw != self._last_raw:
            self._last_raw = raw
            self._changed_at = ticks_ms()
        if elapsed_ms(self._changed_at) >= DEBOUNCE_MS:
            self._stable = raw
        return self._stable

    def raw_value(self):
        """診断用に入力の生値を返す。"""
        return self.pin.value()


class PicoPowerManager:
    """操作中の状態を保持し、ACC変化だけでは操作を再許可しない。"""

    def __init__(self):
        """出力を先に開放し、停止を仮定せずUNKNOWNから開始する。"""
        self.j2_button_line = J2ButtonPulse(PI_J2_BUTTON_CTRL_GPIO)
        self.acc = DebouncedInput(ACC_INPUT_GPIO, Pin.PULL_DOWN)
        self.pi_status = DebouncedInput(PI_STATUS_INPUT_GPIO, Pin.PULL_DOWN)
        self.led = Pin(ONBOARD_LED_GPIO, Pin.OUT, value=0)
        self.state = "UNKNOWN"
        self.fault_message = None
        self._state_since = ticks_ms()
        self._ready_since = None
        self._last_log_ms = ticks_ms()
        print("Pico電源管理開始: LOWは停止未確認 / J2方式=" + J2_CONTROL_MODE)

    def _transition(self, state, message=None):
        """状態変更を記録し、状態ごとの待ち時間をリセットする。"""
        if self.state != state:
            print("電源状態:", self.state, "->", state, message or "")
            self._state_since = ticks_ms()
        self.state = state
        self.fault_message = message

    def _ready(self, status):
        """入力HIGHが連続2秒安定したことを確認する。OS全体の正常性ではない。"""
        if status != 1 or self.pi_status.raw_value() != 1:
            self._ready_since = None
            return False
        if self._ready_since is None:
            self._ready_since = ticks_ms()
        return elapsed_ms(self._ready_since) >= READY_STABLE_MS

    def confirm_stopped(self):
        """保守者がOS停止を別途確認した場合だけ、起動を1回許可する。

        GPIO LOWやタイムアウトから本関数を自動呼出ししてはならない。
        停止中のPiが人手などで再起動された場合、この許可は無効になる。
        """
        if self.state not in ("UNKNOWN", "HALT_UNCONFIRMED", "FAULT"):
            raise RuntimeError("操作中または稼働中は停止確認を受け付けません")
        if self.pi_status.value() != 0 or self.pi_status.raw_value() != 0:
            raise RuntimeError("状態入力が安定したLOWではありません")
        self._transition("STOPPED_CONFIRMED")

    def send_j2_pulses(self, count, label):
        """1回の操作を完了させる。ACC変化で同じ操作を再発行しない。"""
        try:
            for index in range(count):
                print(label, "J2", index + 1, "/", count)
                self.j2_button_line.pulse()
                if index + 1 < count:
                    time.sleep_ms(SHUTDOWN_PULSE_GAP_MS)
        finally:
            self.j2_button_line.release()

    def request_start(self):
        """停止の明示確認とACC ONがそろった場合だけ起動パルスを送る。"""
        if (self.state != "STOPPED_CONFIRMED" or self.acc.value() != 1
                or self.acc.raw_value() != 1 or self.pi_status.raw_value() != 0):
            return False
        # 操作前に許可を消費し、例外時にも重複した押下を防ぐ。
        self._transition("BOOTING")
        self.send_j2_pulses(START_PULSE_COUNT, "起動")
        return True

    def request_shutdown(self):
        """サービス稼働確認中にACC OFFが安定した場合だけ停止要求を送る。"""
        if (self.state != "RUNNING" or self.acc.value() != 0
                or self.acc.raw_value() != 0 or self.pi_status.raw_value() != 1):
            return False
        self._transition("STOPPING")
        self.send_j2_pulses(SHUTDOWN_PULSE_COUNT, "停止要求")
        return True

    def step(self):
        """1監視周期を実行する。待機中もACC・LED・ログを更新する。"""
        acc, status = self.acc.value(), self.pi_status.value()
        ready = self._ready(status)
        if acc is None or status is None:
            return
        if self.state == "UNKNOWN":
            if ready:
                self._transition("RUNNING")
        elif self.state == "RUNNING":
            if status == 0:
                self._transition("UNKNOWN", "通知喪失。OS停止とは判定しません")
            elif acc == 0:
                self.request_shutdown()
        elif self.state == "STOPPED_CONFIRMED":
            # HIGHの生値でも保守許可を失効させ、起動途中への押下を防ぐ。
            if self.pi_status.raw_value() != 0:
                self._transition("UNKNOWN")
            elif acc == 1:
                self.request_start()
        elif self.state == "BOOTING":
            if ready:
                self._transition("RUNNING")
            elif elapsed_ms(self._state_since) >= BOOT_TIMEOUT_MS:
                self._transition("FAULT", "起動通知タイムアウト。再押下しません")
        elif self.state == "STOPPING":
            if status == 0:
                self._transition("HALT_UNCONFIRMED", "通知解除。OS停止完了は未確認です")
            elif elapsed_ms(self._state_since) >= SHUTDOWN_TIMEOUT_MS:
                self._transition("FAULT", "停止要求後の通知解除タイムアウト")
        # HALT_UNCONFIRMEDとFAULTはACC変化/HIGH回復でも解除しない。
        self._heartbeat(acc)
        self._log_state_if_needed()

    def run(self):
        """終了・異常時に接点を必ず開放して監視を終える。"""
        try:
            while True:
                self.step()
                time.sleep_ms(POLL_MS)
        except BaseException:
            self._transition("FAULT", "監視中断。実機状態の確認が必要です")
            raise
        finally:
            try:
                self.j2_button_line.release()
            finally:
                self.led.value(0)

    def _heartbeat(self, acc):
        """状態未確認を停止と区別してLEDに表示する。"""
        now = ticks_ms()
        if self.state in ("FAULT", "HALT_UNCONFIRMED", "UNKNOWN"):
            phase = now % 1000
            on = phase < 100 or 200 <= phase < 300
        elif self.state == "RUNNING":
            on = True
        elif self.state == "STOPPING":
            on = (now // LED_FAST_BLINK_INTERVAL_MS) % 2 == 0
        elif self.state == "BOOTING" or acc == 1:
            on = (now // LED_BLINK_INTERVAL_MS) % 2 == 0
        else:
            on = False
        self.led.value(int(on))

    def _log_state_if_needed(self):
        """USBシリアルへ2秒ごとに入力・操作状態・未確認理由を出力する。"""
        if elapsed_ms(self._last_log_ms) >= STATE_LOG_INTERVAL_MS:
            self._last_log_ms = ticks_ms()
            print("状態:", self.state, "ACC:", self.acc.value(),
                  "通知:", self.pi_status.value(), "詳細:", self.fault_message)


def main():
    """Pico上の常駐処理を開始する。インポート時にはGPIOを操作しない。"""
    PicoPowerManager().run()


if __name__ == "__main__":
    main()
