# L880K OBD2 / K-Line TestProgram

L880KコペンのECUへ読み取り専用で接続し、K-Line方式の候補と要求応答時間を確認するコンソールアプリです。

## 対応範囲

- ELM327互換USB-OBD変換器を対象にする
- ISO 9141-2（ELM327 Protocol 3）を確認する
- ISO 14230-4 KWP 5-baud（Protocol 4）を確認する
- ISO 14230-4 KWP Fast（Protocol 5）を確認する
- `0100`、`010C`、`010D`、`0105`などの読み取り要求の応答時間を計測する
- ELM327のUSBシリアル通信だけを扱い、K-LineをRaspberry PiのGPIOへ直接接続しない

このツールは、車両制御、故障コード消去、ECU書き換えを行いません。ただし、車両通信機器を接続するため、配線とアダプタの適合を確認してから使用してください。

## インストール

Raspberry Pi 5またはDebian 13で実行します。

```bash
cd TestProgram
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windowsの場合は、`source`の代わりに次を使用します。

```powershell
cd TestProgram
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## ポート確認

Linux:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
lsusb
```

Windows:

```powershell
Get-CimInstance Win32_SerialPort | Select-Object DeviceID,Description
```

USB機器が認識されても、ECU通信が成立したことにはなりません。アプリの初期化方式判定でECUのデータ応答を確認してください。

## 実行例

Linuxの例です。

```bash
python l880k_kline_test.py --port /dev/ttyUSB0
```

Windowsの例です。

```powershell
python l880k_kline_test.py --port COM5
```

ELM327のUSBシリアル速度が異なる場合は変更します。

```bash
python l880k_kline_test.py --port /dev/ttyUSB0 --serial-baud 9600
```

特定の方式だけを計測する場合は、`3`、`4`、`5`を指定します。

```bash
# ISO 9141-2
python l880k_kline_test.py --port /dev/ttyUSB0 --protocol 3

# KWP2000 Fast
python l880k_kline_test.py --port /dev/ttyUSB0 --protocol 5
```

要求を変更して時間を計測する場合は、カンマ区切りの16進数で指定します。

```bash
python l880k_kline_test.py \
  --port /dev/ttyUSB0 \
  --measure-queries 0100,010C,010D,0105 \
  --repeat 10 \
  --interval 0.5
```

## 結果の見方

- `DATA`: ECU由来と判断できる16進数応答を受信
- `NO DATA`: 方式は選択できたが、その要求に対するデータなし
- `UNABLE`: ELM327がECU初期化または通信を完了できない
- `NO RESPONSE`: 応答なし、またはタイムアウト

方式判定は、データ応答数が多く、応答時間が短い候補を表示します。候補が出ても、車両OFF/ON後に複数回再現するまで設計へ採用しないでください。

時間計測では、要求ごとに次を表示します。

- 応答時間
- 受信データのバイト数
- 成功回数
- 平均、最小、最大応答時間

## 注意

1. 最初はL880Kで接続実績のあるELM327 v1.5系で試験してください。
2. `ATSP`で方式を切り替えますが、方式の切替はアダプタ内部の初期化を伴います。
3. `0100`などの要求が失敗しても、直ちにECU非対応とは断定しません。L880K固有の要求が必要な可能性があります。
4. 生バイト転送型K-Lineアダプタは、L880Kの要求フレームが確定してから別途対応します。未確認フレームの総当たり送信は行いません。
5. OBD端子が常時給電の場合があるため、試験後はアダプタを取り外してください。

実車で確認した初期化方式、要求フレーム、応答フレーム、換算式は、`docs/SW設計/付録5_L880K_OBD2_K-Line初期化手順.md`と09 OBD2車両情報取得のプロファイルへ反映します。
