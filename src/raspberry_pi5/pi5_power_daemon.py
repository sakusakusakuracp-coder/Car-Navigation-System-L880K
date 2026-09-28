#!/usr/bin/env python3
"""
Raspberry Pi 5 電源状態通知デーモン。

本プロセスは、設計書の以下の内容に対応するRaspberry Pi 5側の常駐処理である。

- 4.1.5.1 起動時の内部処理
- 4.1.5.2 シャットダウン時の内部処理
- 4.1.5.4 Raspberry Pi 5 GPIO対応表(電源関係)

設計書上の信号割り当ては以下とする。

- GPIO 26: Raspberry Pi Picoへの通知サービス稼働出力。通知中はHIGH。

起動指示およびシャットダウン指示は、Pico側からPi 5のJ2電源ボタン端子を
短時間短絡することで行う。本デーモンは、その指示を受け取らず、
本サービスの通知出力をPicoへ伝える役割だけを持つ。

設計意図:

- デーモン未起動時および停止処理中はGPIO 26をLOWにする。
- OS起動後、本デーモンが開始された時点でGPIO 26をHIGHにする。
- LOWはサービス停止・未起動・断線などを含み、OS停止完了を意味しない。
"""

from __future__ import annotations

import logging
import os
import signal
import threading

# Pi 5からPicoへ稼働状態を通知するためのBCM GPIO番号。
# 設計上の標準はGPIO 26、物理ピン37である。環境変数による上書きは、
# 実験時や一時的な配線変更時のために残している。
STATUS_GPIO = int(os.getenv("PI5_STATUS_GPIO", "26"))


class Pi5PowerDaemon:
    """Pi 5の稼働状態通知GPIOを制御するクラス。

    このデーモンはシャットダウン要求を受け取らず、J2電源ボタン端子も操作しない。
    起動・シャットダウン指示はPico側回路が担当する。本クラスの通知だけでは、
    OS全体の正常性、停止完了、ファイル同期完了を判定できない。
    """

    def __init__(self, output_factory=None) -> None:
        """GPIO 26をLOW出力として準備し、デーモン起動前の誤通知を防ぐ。"""
        # signalハンドラとメインループで共有する停止要求フラグ。
        # セットされるとrun()の待機ループが終了し、stop()でGPIOをLOWへ戻す。
        self._stop_requested = threading.Event()
        self._closed = False
        if not 0 <= STATUS_GPIO <= 27:
            raise ValueError("PI5_STATUS_GPIOはBCM番号0から27で指定してください")
        if output_factory is None:
            from gpiozero import OutputDevice
            output_factory = OutputDevice

        # initial_value=Falseにより、run()で明示的にon()するまではLOWを維持する。
        # LOWは通知未開始であり、Pi 5停止の証明にはならない。
        self.status_output = output_factory(
            STATUS_GPIO,
            active_high=True,
            initial_value=False,
        )

    def run(self) -> None:
        """状態通知GPIOをHIGHにして、停止要求が来るまで常駐する。"""
        logging.info(
            "Pi 5 power daemon started: status GPIO=%s",
            STATUS_GPIO,
        )

        if self._stop_requested.is_set() or self._closed:
            return
        # HIGHは通知サービスの稼働を示し、全サービスの準備完了ではない。
        self.status_output.on()
        logging.info("Status output set HIGH")

        # 1秒ごとに停止要求を確認する。SIGTERM/SIGINTを受けると、
        # request_stop()経由で_stop_requestedがセットされる。
        while not self._stop_requested.wait(timeout=1.0):
            pass

    def request_stop(self) -> None:
        """シグナル処理では停止要求だけを記録し、GPIO操作を割り込ませない。"""
        self._stop_requested.set()

    def stop(self) -> None:
        """通知を解除する。OS停止完了ではない。二度目の解放は行わない。"""
        logging.info("Stopping Pi 5 power daemon")
        self._stop_requested.set()
        if self._closed:
            return
        self._closed = True
        try:
            self.status_output.off()
        finally:
            self.status_output.close()
        logging.info("Status notification cleared; OS halt is NOT confirmed")


def configure_logging() -> None:
    """systemd / journalctlで確認しやすい形式にログを設定する。"""
    logging.basicConfig(
        level=os.getenv("PI5_POWER_DAEMON_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def main() -> int:
    """systemdまたはコンソールから停止要求を受けるまでデーモンを実行する。"""
    configure_logging()
    daemon = Pi5PowerDaemon()

    def request_stop(signum: int, _frame: object) -> None:
        """SIGTERM / SIGINTを受けたときに、安全にデーモンを停止する。"""
        logging.info("Received signal %s", signum)
        daemon.request_stop()

    try:
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        daemon.run()
    finally:
        daemon.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
