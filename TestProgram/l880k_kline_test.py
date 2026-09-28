#!/usr/bin/env python3
"""L880K向けOBD2/K-Line通信確認用の読み取り専用コンソールアプリ。

ELM327互換USB-OBD変換器を通して、ISO 9141-2とKWP2000の初期化候補を
順番に確認する。車両制御、故障コード消去、ECU書き換えは実行しない。
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Iterable

try:
    import serial
    from serial import SerialException
except ImportError:  # pragma: no cover - 実行環境の依存関係エラーをわかりやすく表示する
    serial = None

    class SerialException(Exception):
        """pyserial未導入時の代替例外。"""


PROTOCOLS: dict[str, tuple[str, str]] = {
    "3": ("ISO 9141-2", "5-baud初期化 / 10400 baud"),
    "4": ("ISO 14230-4 KWP", "5-baud初期化 / 10400 baud"),
    "5": ("ISO 14230-4 KWP", "Fast初期化 / 10400 baud"),
}

DEFAULT_PROBE_QUERIES = ("0100", "010C", "010D", "0105")
HEX_LINE = re.compile(r"^(?:[0-9A-F]{2}(?:\s+|$))+$", re.IGNORECASE)


@dataclass(frozen=True)
class Response:
    """1回のELM327要求についての応答と計測結果。"""

    command: str
    raw_text: str
    elapsed_ms: float

    @property
    def lines(self) -> list[str]:
        """表示用に空行とELMのプロンプトを除いた応答行を返す。"""
        result: list[str] = []
        command = self.command.strip().upper()
        for line in self.raw_text.replace("\r", "\n").split("\n"):
            line = line.strip()
            if not line or line == ">" or line.upper() == command:
                continue
            result.append(line)
        return result

    @property
    def hex_lines(self) -> list[str]:
        """16進数の応答行だけを返す。"""
        return [line for line in self.lines if HEX_LINE.fullmatch(line)]

    @property
    def has_data(self) -> bool:
        """ECU由来と思われる16進数データを受信したか。"""
        return bool(self.hex_lines)

    @property
    def status(self) -> str:
        """応答の簡易判定を返す。"""
        joined = " ".join(self.lines).upper()
        if self.has_data and not any(token in joined for token in ("NO DATA", "ERROR", "UNABLE")):
            return "DATA"
        if "NO DATA" in joined:
            return "NO DATA"
        if "UNABLE" in joined:
            return "UNABLE"
        if "ERROR" in joined or "?" in joined:
            return "ERROR"
        return "NO RESPONSE"


class Elm327Client:
    """ELM327のASCIIコマンドを扱う小さな同期クライアント。"""

    def __init__(self, port: str, baudrate: int, timeout: float) -> None:
        if serial is None:
            raise RuntimeError("pyserialがありません。python3 -m pip install -r requirements.txt を実行してください")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.connection = None

    def __enter__(self) -> "Elm327Client":
        self.connection = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            write_timeout=1.0,
        )
        self.connection.reset_input_buffer()
        self.connection.reset_output_buffer()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.connection is not None:
            self.connection.close()

    def command(self, command: str, timeout: float | None = None) -> Response:
        """ELM327へ読み取り系コマンドを送り、プロンプトまでの時間を測定する。"""
        if self.connection is None:
            raise RuntimeError("シリアルポートが開かれていません")
        command = command.strip().upper()
        started = time.monotonic()
        self.connection.reset_input_buffer()
        self.connection.write((command + "\r").encode("ascii"))
        self.connection.flush()

        limit = started + (self.timeout if timeout is None else timeout)
        received = bytearray()
        while time.monotonic() < limit:
            waiting = self.connection.in_waiting
            chunk = self.connection.read(waiting or 1)
            if chunk:
                received.extend(chunk)
                if b">" in received:
                    break

        elapsed_ms = (time.monotonic() - started) * 1000.0
        return Response(command, received.decode("ascii", errors="replace"), elapsed_ms)


def print_response(response: Response, prefix: str = "") -> None:
    """応答を人間が読みやすい形式で表示する。"""
    lines = response.lines or ["(応答なし)"]
    print(f"{prefix}{response.command:<8} {response.elapsed_ms:8.1f} ms  {response.status}")
    for line in lines:
        print(f"    {line}")


def configure_client(client: Elm327Client, reset: bool) -> None:
    """ELM327を表示抑制・エコー無効の安全な試験状態へ設定する。"""
    if reset:
        response = client.command("ATZ", timeout=5.0)
        print_response(response, "初期化  ")
        time.sleep(1.0)
    for command in ("ATE0", "ATL0", "ATS0", "ATH1", "ATAT1"):
        print_response(client.command(command), "設定    ")


def select_protocol(client: Elm327Client, protocol: str) -> Response:
    """ELM327のプロトコル番号を選択する。"""
    return client.command(f"ATSP{protocol}")


def protocol_probe(client: Elm327Client, queries: Iterable[str]) -> list[dict[str, object]]:
    """K-Line系候補を試し、応答時間とデータ有無を集計する。"""
    results: list[dict[str, object]] = []
    for protocol, (name, init) in PROTOCOLS.items():
        print(f"\n--- Protocol {protocol}: {name} ({init}) ---")
        selection = select_protocol(client, protocol)
        print_response(selection, "方式選択")
        responses: list[Response] = []
        for query in queries:
            response = client.command(query)
            responses.append(response)
            print_response(response, "要求    ")
        data_responses = [response for response in responses if response.has_data]
        median_ms = statistics.median(response.elapsed_ms for response in responses) if responses else None
        results.append(
            {
                "protocol": protocol,
                "name": name,
                "init": init,
                "data_count": len(data_responses),
                "query_count": len(responses),
                "median_ms": median_ms,
            }
        )
    return results


def choose_protocol(results: list[dict[str, object]]) -> str | None:
    """データ応答数と時間から候補を1つ選ぶ。"""
    usable = [item for item in results if int(item["data_count"]) > 0]
    if not usable:
        return None
    usable.sort(key=lambda item: (-int(item["data_count"]), float(item["median_ms"] or 10**9)))
    return str(usable[0]["protocol"])


def print_probe_summary(results: list[dict[str, object]], selected: str | None) -> None:
    """候補方式の比較結果を表示する。"""
    print("\n=== 初期化方式の判定結果 ===")
    print("方式  データ応答  中央値(ms)  内容")
    for item in results:
        print(
            f"{item['protocol']:>4}  {item['data_count']}/{item['query_count']:<10} "
            f"{(item['median_ms'] or 0):>10.1f}  {item['name']} / {item['init']}"
        )
    if selected is None:
        print("判定: ECUデータ応答を確認できませんでした。ケーブル、キーON、方式、電源を確認してください。")
    else:
        print(f"判定候補: Protocol {selected}。実車OFF/ON後に複数回再現してから採用してください。")


def measure_queries(client: Elm327Client, queries: Iterable[str], repeat: int, interval: float) -> None:
    """指定要求の応答時間、長さ、成功率を計測する。"""
    print("\n=== 要求ごとの時間計測 ===")
    for query in queries:
        responses: list[Response] = []
        print(f"\n[{query}] {repeat}回")
        for index in range(repeat):
            response = client.command(query)
            responses.append(response)
            byte_count = sum(len(re.findall(r"[0-9A-Fa-f]{2}", line)) for line in response.hex_lines)
            print(f"  {index + 1:>2}: {response.elapsed_ms:8.1f} ms, {byte_count:>3} bytes, {response.status}")
            if index + 1 < repeat:
                time.sleep(interval)
        data_count = sum(response.has_data for response in responses)
        times = [response.elapsed_ms for response in responses]
        print(
            f"  結果: 成功 {data_count}/{repeat}, 平均 {statistics.mean(times):.1f} ms, "
            f"最小 {min(times):.1f} ms, 最大 {max(times):.1f} ms"
        )


def parse_queries(value: str) -> list[str]:
    """カンマ区切りの16進要求を検証する。"""
    queries = [item.strip().upper() for item in value.split(",") if item.strip()]
    if not queries:
        raise argparse.ArgumentTypeError("要求を1つ以上指定してください")
    for query in queries:
        if not re.fullmatch(r"[0-9A-F]+", query) or len(query) % 2:
            raise argparse.ArgumentTypeError(f"16進数として解釈できません: {query}")
    return queries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="L880K用OBD2/K-Line読み取り試験ツール")
    parser.add_argument("--port", required=True, help="USB-OBD変換器のポート。例: /dev/ttyUSB0, COM5")
    parser.add_argument("--serial-baud", type=int, default=38400, help="ELM327とのUSBシリアル速度。既定値: 38400")
    parser.add_argument("--timeout", type=float, default=5.0, help="1要求あたりの待機時間(秒)。既定値: 5")
    parser.add_argument("--probe-queries", type=parse_queries, default=list(DEFAULT_PROBE_QUERIES), help="方式判定に使う要求。既定: 0100,010C,010D,0105")
    parser.add_argument("--protocol", choices=tuple(PROTOCOLS), help="方式判定を省略して指定方式で計測")
    parser.add_argument("--measure-queries", type=parse_queries, default=list(DEFAULT_PROBE_QUERIES), help="時間計測する要求。既定: 0100,010C,010D,0105")
    parser.add_argument("--repeat", type=int, default=3, help="時間計測の繰返し回数。既定値: 3")
    parser.add_argument("--interval", type=float, default=0.3, help="要求間隔(秒)。既定値: 0.3")
    parser.add_argument("--no-reset", action="store_true", help="接続時のATZを省略する")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeatは1以上にしてください")
    if args.interval < 0:
        parser.error("--intervalは0以上にしてください")

    print("L880K OBD2/K-Line通信確認ツール")
    print("読み取り専用: ECU書き換え・故障コード消去・車両制御は実行しません")
    print(f"ポート: {args.port} / USBシリアル速度: {args.serial_baud}")
    print("車両をACCまたはONにし、エンジン停止状態で実行してください。")

    try:
        with Elm327Client(args.port, args.serial_baud, args.timeout) as client:
            configure_client(client, reset=not args.no_reset)
            if args.protocol is None:
                results = protocol_probe(client, args.probe_queries)
                selected = choose_protocol(results)
                print_probe_summary(results, selected)
                if selected is None:
                    return 2
                protocol = selected
                print(f"\n判定候補 Protocol {protocol} を再選択して時間計測を行います。")
                print_response(select_protocol(client, protocol), "方式選択")
            else:
                protocol = args.protocol
                print(f"\n指定方式 Protocol {protocol} を選択します。")
                print_response(select_protocol(client, protocol), "方式選択")

            print(f"\nProtocol {protocol}で時間計測を開始します。")
            measure_queries(client, args.measure_queries, args.repeat, args.interval)
            print("\n試験終了。アダプタを取り外す場合は車両をOFFにしてから行ってください。")
            return 0
    except (SerialException, OSError) as error:
        print(f"シリアル通信エラー: {error}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\n中断しました。車両をOFFにしてからアダプタを取り外してください。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
