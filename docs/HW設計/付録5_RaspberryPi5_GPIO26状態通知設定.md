# 付録5: Raspberry Pi 5 GPIO26状態通知設定

本付録では、GPIO26（物理ピン37）でPi 5状態通知サービスの稼働をPicoへ伝える設定を示す。LOWをOS停止完了として扱わない。Pi 5側とPico側の両コードを更新する。

## 1. 目的

Pi 5の3.3V電源ピンは状態通知には使用しない。常駐サービスがGPIO26をHIGHへ出力し、PicoはGP10で監視する。HIGHは通知サービスが出力を開始したこと、LOWは通知なしを意味する。サービス停止・未起動・異常・断線とOS停止をGPIO1本では区別できない。

| 項目 | 設定 |
| --- | --- |
| Pi 5側GPIO | GPIO 26（BCM番号26、物理ピン37） |
| 信号方向 | Pi 5からPicoへの出力 |
| 通知中 | サービスがHIGHを出力。全アプリの準備完了や生存監視は保証しない |
| 通知なし | LOWまたはHi-ZをPico側プルダウンでLOW扱い。OS停止完了ではない |
| Pico側入力 | GP10（物理ピン14） |
| Pi 5側コード定数 | `STATUS_GPIO` |
| Pico側コード定数 | `PI_STATUS_INPUT_GPIO` |

## 2. 配線

GPIO 26はPicoのGP10へ接続し、Pi 5 GNDとPico GNDを共通化する。GPIO信号は3.3V系として扱い、5Vや車体12Vを接続しない。

```text
Raspberry Pi 5                       Raspberry Pi Pico

GPIO 26 / 物理ピン37  ------------->  GP10 / 物理ピン14
GND                  --------------  GND
```

設計上の注意点は以下とする。

- Pi 5の3.3Vピンを状態通知信号として使用しない。
- GPIO 26へ外部電圧を印加しない。
- Pico側はプルダウン入力とし、Pi 5停止時やGPIO未駆動時をLOWとして扱う。
- 配線が長くなる場合は、必要に応じて直列抵抗やノイズ対策を追加する。

## 3. 必要パッケージ

Pi 5上でGPIOを制御するため、Raspberry Pi OSに`gpiozero`と`lgpio`系パッケージを導入する。

```bash
sudo apt update
sudo apt install -y python3-gpiozero python3-lgpio
```

## 4. 常駐スクリプト

本リポジトリのPi 5側状態通知スクリプトを使用する。

```text
src/raspberry_pi5/pi5_power_daemon.py
```

このスクリプトは、起動するとGPIO 26をHIGHにし、サービス停止時にLOWへ戻す。既定のGPIO番号は以下の定数で定義する。

```python
STATUS_GPIO = int(os.getenv("PI5_STATUS_GPIO", "26"))
```

GPIO番号を変更する場合は、環境変数`PI5_STATUS_GPIO`で上書きできる。ただし、本設計ではGPIO 26を標準とする。

## 5. 配置例

Pi 5上に配置先ディレクトリを作成し、スクリプトを配置する。

```bash
sudo mkdir -p /opt/copen-navi
sudo cp src/raspberry_pi5/pi5_power_daemon.py /opt/copen-navi/pi5_power_daemon.py
sudo chmod 755 /opt/copen-navi/pi5_power_daemon.py
```

上記はリポジトリのルートで実行する。Picoにも更新したpico_power_manager.pyを配置する。J2の極性設定は実回路を確認して選ぶ。Picoだけ旧コードのままではLOWの誤判定は修正されない。

## 6. systemdサービス設定

リポジトリのユニットを配置する。

```bash
sudo cp src/raspberry_pi5/systemd/pi5-power-daemon.service /etc/systemd/system/
```

主要設定は以下のとおり。After=multi-user.targetとWantedBy=multi-user.targetを併用せず、起動順序の循環を避ける。

```ini
[Unit]
Description=L880K Pi 5 notification service GPIO (not halt acknowledgement)
After=basic.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
Environment=PI5_STATUS_GPIO=26
ExecStart=/usr/bin/python3 /opt/copen-navi/pi5_power_daemon.py
Restart=on-failure
RestartSec=2
TimeoutStopSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

サービスを有効化して起動する。

```bash
sudo systemctl daemon-reload
sudo systemctl enable pi5-power-daemon.service
sudo systemctl start pi5-power-daemon.service
```

## 7. 動作確認

サービス状態を確認する。

```bash
systemctl status pi5-power-daemon.service
```

ログでGPIO 26がHIGHへ設定されたことを確認する。

```bash
journalctl -u pi5-power-daemon.service -n 50 --no-pager
```

GPIO状態は、使用環境に応じて`pinctrl`などで確認する。

```bash
pinctrl get 26
```

Pico側では、HIGHが安定してRUNNINGへ移ることを確認する。サービスだけを再起動してLOWになった場合、UNKNOWNへ移り、J2起動パルスが出ないことを確認する。停止要求後のLOWはHALT_UNCONFIRMEDを保持し、ACCをONに戻してもJ2を再操作しない。

**運用制約:** 停止後のACC連動自動再起動は保留。OS停止の別途確認なしに起動を許可しない。保守確認の手順は[14 Pico電源管理](../SW設計/詳細設計/14_Pico電源管理.md#dd-8)を参照する。固定時間待ちやサービス停止だけを停止確認の代わりにしない。

## 8. トラブルシュート

| 症状 | 確認点 |
| --- | --- |
| Pico側で常にLOWになる | Pi 5のサービスが起動しているか、GPIO 26とPico GP10が接続されているか、GNDが共通化されているかを確認する |
| Pico側で常にHIGHになる | Pico側入力がプルダウンになっているか、GPIO 26以外の3.3V電源へ誤接続していないかを確認する |
| サービスが起動しない | `python3-gpiozero`、`python3-lgpio`がインストール済みか、`ExecStart`のパスが正しいかを確認する |
| GPIO番号が合わない | 本設計ではBCM番号26を使用する。物理ピン番号37と混同しない |
| 停止後にACC ONでも起動しない | HALT_UNCONFIRMEDの保護動作。LOWだけではOS停止を保証できないため、実機状態を別途確認する |
| 通知サービスだけの再起動でJ2が操作される | Picoに旧版が残っていないか確認する |

## 9. 設計書上の対応箇所

- [4.1.4.4 Raspberry Pi Pico GPIO対応表(電源関係)](ハードウェア設計書.md#sec-4144)
- [4.1.5.3 Raspberry Pi 5 GPIO対応表(電源関係)](ハードウェア設計書.md#sec-4153)
