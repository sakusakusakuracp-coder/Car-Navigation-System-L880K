# 付録5 L880K OBD2・K-Line初期化確認手順

<a id="app5-1"></a>

## 1. 目的

L880KコペンのECUへ、Raspberry Pi 5からOBD2コネクタ経由で接続するための実車確認手順を示す。

本付録は通信方式を確定するための試験手順であり、記載した初期化フレームがL880Kで確認済みであることを意味しない。実車から有効な応答を確認した方式だけを、09 OBD2車両情報取得のL880K用プロファイルへ登録する。

<a id="app5-2"></a>

## 2. 前提と安全条件

- 対象はL880Kの読み取り専用診断通信とする。
- ECU書き換え、故障コード消去、アクチュエータ試験、車両制御要求は送信しない。
- 車両の配線図でOBD端子の電源、GND、K-Lineを確認してから接続する。
- K-LineをRaspberry PiのGPIOやUSB-TTL変換器へ直接接続しない。
- ISO K-Lineトランシーバを内蔵したUSB-OBD変換器を使用する。
- OBD端子は常時給電の場合があるため、試験終了後はアダプタを取り外すか、車両側の電源条件を確認する。
- 初回試験は換気された場所で行い、輪止めを使用する。走行中に通信確認を行わない。

<a id="app5-3"></a>

## 3. 用意するもの

| 項目 | 条件 |
| --- | --- |
| Raspberry Pi 5 | Debian 13または採用するRaspberry Pi OS、USBポート使用可能 |
| USB-OBD変換器 | L880Kで接続実績のあるELM327 v1.5系、または生バイト転送可能なK-Line対応品 |
| OBD延長ケーブル | 端子番号と配線を確認できるもの。必要な場合だけ使用 |
| USBシリアル確認ツール | `dmesg`、`lsusb`、`python3`、`pyserial`など |
| 記録手段 | 通信ログ、車両年式、ECU型式、アダプタ型番を記録する |

L880Kでは、一般的な自動認識だけでなく「DAIHATSU K-Line」または「JDM K-Line」を選択して接続できた例がある。ただし、アダプタや車両年式によって結果が変わるため、実車で確認する。

参考：

- [L880KでのJDM K-Line接続例](https://copen.site/ijiri/1503/)
- [L880KでのDAIHATSU K-Line接続例](https://minkara.carview.co.jp/userid/3147599/car/2787543/5387029/note.aspx)

<a id="app5-4"></a>

## 4. 物理接続確認

1. 車両のイグニッションをOFFにする。
2. OBDコネクタの電源、GND、K-Lineの端子番号を車両資料で確認する。
3. USB-OBD変換器を車両へ接続する。
4. 変換器をRaspberry Pi 5のUSBポートへ接続する。
5. Raspberry Pi 5でUSB機器とシリアルデバイスを確認する。

```bash
lsusb
dmesg --follow
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

USB機器が認識されても、ECUとの通信が成立したとは判断しない。通信成立は、初期化後にECUから検証可能な応答を受信した時点とする。

<a id="app5-5"></a>

## 5. 初期化方式の確認順序

L880K用プロファイルは、次の順で候補を確認する。各試行の前に通信セッションを終了し、車両をOFFにしてから再試行する。

### 5.1 ELM327での接続確認

最初の適合確認では、L880Kで接続実績のあるELM327 v1.5系のUSBアダプタを使用し、診断ソフト側で次の方式を選択する。

```text
メーカー: DAIHATSU
プロトコル: DAIHATSU K-Line または JDM K-Line
```

この試験の目的は、L880K ECUが応答することと、取得可能な項目を確認することである。ELM327の内部初期化をそのまま自作ソフトの通信仕様とは扱わない。

### 5.2 Fast Initialization候補

KWP2000系の候補として、K-Lineを短時間LOWにした後HIGHへ戻し、診断開始要求を送信する方式を確認する。一般的な例ではLOW約25ms、HIGH約25msのウェイクパターンが使われるが、時間と要求フレームはL880Kで実測して確定する。

```text
K-Line idle HIGH
  ↓
LOW 約25ms
  ↓
HIGH 約25ms
  ↓
診断開始要求
  ↓
ECU応答を検査
```

初期化候補の例をそのまま車両へ送信してはいけない。要求先アドレス、テスターアドレス、フレーム形式、チェックサムは実測または信頼できる車種資料で確認する。

### 5.3 5-baud Initialization候補

ISO 9141/KWP系の候補として、5 baudで初期アドレスを送信し、ECUから同期バイトとキーバイトを受け取る方式を確認する。

```text
K-LineをアイドルHIGHで保持
  ↓
5 baudで初期アドレス送信
  ↓
ECUから0x55とキーバイトを受信
  ↓
キーバイトに対する応答を送信
  ↓
ECUからアドレス確認を受信
  ↓
通常通信速度へ切り替え
```

5 baud送信は通常のUSBシリアル設定だけでは正確に実行できない場合がある。その場合は、アダプタ内部の初期化機能を使うか、K-Lineトランシーバの送信制御機能を確認する。

### 5.4 通常通信速度

通常通信速度は、まず次を候補にする。

```text
通信速度: 10400 baud
データ形式: 8N1
```

ECUの同期応答や仕様資料で別の速度が示された場合は、実測値を優先する。

<a id="app5-6"></a>

## 6. ECU応答の確認

初期化成功後、次の条件をすべて満たす場合だけ通信成立と判定する。

| 確認項目 | 合格条件 |
| --- | --- |
| 応答有無 | ECUから応答バイトを受信する |
| 応答形式 | 初期化方式に合う同期・キーバイト・ヘッダを確認できる |
| 要求対応 | 送信した要求に対応する応答である |
| チェックサム | 採用プロトコルの検査に合格する |
| 再現性 | 車両OFF/ON後の再接続で複数回成功する |
| 読取値 | 車速、回転数、水温などの値が妥当な範囲で変化する |

USBポートが開けたこと、アダプタのLEDが点灯したこと、ELM327から`OK`が返ったことだけではECU接続成功とは判定しない。

<a id="app5-7"></a>

## 7. 通信ログの記録

次の情報を1回の試験単位で記録する。

```text
vehicle: L880K
model_year: 未記入の場合は不明と記録
ecu_id: 実測値
adapter_model: 実物の型番
usb_device: /dev/ttyUSB0 など
init_method: fast / 5-baud / ELM327内部処理
baudrate: 実測値
tester_address: 実測値
ecu_address: 実測値
request_frame: 実測値
response_frame: 実測値
checksum: 実測方式
result: SUCCESS / FAIL / UNKNOWN
```

ログには、故障コード消去や書き換えなどの制御要求を含めない。生ログに車両識別情報や位置情報が含まれる場合は、公開リポジトリへ登録しない。

<a id="app5-8"></a>

## 8. L880K用プロファイルへの反映

実車で再現性を確認した後、09 OBD2車両情報取得の設定へ次の情報を登録する。

```yaml
vehicle: l880k
protocol: daihatsu_kline
transport: usb_serial
baudrate: 10400
data_bits: 8
parity: none
stop_bits: 1
init_method: TBD
tester_address: TBD
ecu_address: TBD
supported_queries: []
```

`TBD`のまま通常取得を有効化してはいけない。車速・回転数・水温などは、要求フレーム、応答位置、単位、換算式を項目ごとに確認してから登録する。

<a id="app5-9"></a>

## 9. 不具合時の切り分け

| 状態 | 確認内容 |
| --- | --- |
| USB機器が見えない | USBケーブル、電源、udev権限、`/dev/ttyUSB*`の変化 |
| USBは開けるがECU応答なし | キーON、OBD電源、GND、K-Line端子、アダプタ適合 |
| 初期化だけ失敗 | DAIHATSU/JDM K-Line選択、Fast/5-baud方式、通信速度、車両OFF/ON後の再試行 |
| 初期化成功後に読取失敗 | 要求ID、ECUアドレス、フレーム形式、チェックサム、項目別換算式 |
| 一部項目だけ失敗 | ECUが対応する項目だけをSUPPORTEDとし、未確認項目を0で補わない |
| 再接続で不安定 | OBD常時給電、ECUスリープ、アダプタの待機電流、セッション終了処理 |

<a id="app5-10"></a>

## 10. 採用判定

次の条件を満たすまで、OBD2車両情報取得を現在地補正や走行中の安全判定へ使用しない。

- L880K実車で初期化と再接続に成功する。
- 車両OFF/ON後に同じ手順で再現する。
- 車速、回転数、水温の要求と換算を確認する。
- USB切断、ECU無応答、範囲外値を検出できる。
- 通信断時に過去値を使用せず、UIへUNKNOWNを通知できる。
- 読み取り専用であることを確認する。

初期化方式が確定するまでは、09 OBD2車両情報取得の状態を`T.B.D`または`UNKNOWN`とし、GPSのみの動作を妨げない。

<a id="app5-11"></a>

## 11. TestProgramによる確認

実車確認用のコンソールアプリは、リポジトリ直下の`TestProgram`に配置する。

```bash
cd TestProgram
python3 -m pip install -r requirements.txt
python3 l880k_kline_test.py --port /dev/ttyUSB0
```

Windowsでは、ポート名を`COM5`などへ変更する。

```powershell
cd TestProgram
python l880k_kline_test.py --port COM5
```

アプリはELM327のProtocol 3、4、5を順番に選択し、読み取り要求に対する応答の有無と時間を比較する。結果の候補は自動確定せず、車両OFF/ON後の再現試験と通信ログ確認を行ってから、L880K用プロファイルへ反映する。
