# L880K Car Navigation System

ダイハツ コペン L880K向けの車載インフォテインメントシステムです。Raspberry Pi 5をメインコントローラとして、自作UI、Waydroid上のOsmAnd、LIVI、車両情報取得、現在地補正、カメラ・録画、音楽再生などを統合します。

## 構成

- **メインコントローラ**: Raspberry Pi 5
- **サブコントローラ**: Raspberry Pi 3B（カメラ・車体信号処理）
- **待機・電源制御**: Raspberry Pi Pico
- **UI**: PySide6 / QML
- **ナビゲーション**: Waydroid + OsmAnd
- **CarPlay / Android Auto連携**: LIVI
- **車両情報**: USB-OBD2ケーブル経由のK-Line通信
- **測位**: USB GPS。IMUは将来追加予定

## リポジトリ

- [開発用リポジトリ](https://github.com/sakusakusakuracp-coder/Car-Navigation-System-L880K-Development)
- [公開用リポジトリ](https://github.com/sakusakusakuracp-coder/Car-Navigation-System-L880K)

## ディレクトリ

```text
docs/HW設計/       ハードウェア設計書
docs/SW設計/       ソフトウェア設計書・詳細設計書
src/raspberry_pi5/ Raspberry Pi 5向けソースコード
src/raspberry_pi3b/ Raspberry Pi 3B向けソースコード
src/raspberry_pico/ Raspberry Pi Pico向けソースコード
scripts/           Debian 13テスト用スクリプト
tests/             自動テスト
TestProgram/       OBD2通信方式の確認用コンソールアプリ
```

## Debian 13テスト環境

Debian 13上で、SwayのネストしたセッションにUI、Waydroid、各管理サービスをまとめて起動できます。あらかじめ次のソフトウェアをインストールしてください。

```bash
sudo apt install sway dbus-daemon waydroid python3
```

プロジェクトのルートディレクトリで実行します。

```bash
./scripts/start_debian13_sway_ui_test.sh
```

終了時は別の端末から次を実行します。

```bash
./scripts/stop_debian13_ui_test.sh
```

起動スクリプトは、Waydroidコンテナ・セッション、Waydroidナビ管理、OBD2、現在地補正、LIVI連携サービス、自作UIを順番に起動します。管理者認証は、ネストしたSwayを起動する前に求められます。

### 描画方式の切り替え

VirtualBoxでは、次のように描画方式を指定できます。

```bash
# 互換性優先。VirtualBoxの標準テスト設定
L880K_NAV_RENDERER=pixman ./scripts/start_debian13_sway_ui_test.sh

# 3Dアクセラレーションを試す場合
L880K_NAV_RENDERER=gles2 ./scripts/start_debian13_sway_ui_test.sh
```

## LIVI表示に関する注意

LIVIは内部でも画面合成処理を行い、外側のSwayがLIVIウィンドウを固定領域へ配置します。VirtualBoxのソフトウェア描画（llvmpipe）では、LIVI内部の合成処理とwlroots/EGL/DMA-BUFの組み合わせで画面が消える場合があります。

診断目的で内部コンポジタだけを無効にする場合は、次を使用します。

```bash
./scripts/stop_debian13_ui_test.sh
pkill -TERM -u "$(id -u)" -x livi || true
sleep 3
LIVI_NO_COMPOSITOR=1 DEBUG=1 ./scripts/start_debian13_sway_ui_test.sh
```

これはLIVI本体や連携サービスを無効にする設定ではありません。ただし、VirtualBox用の切り分け手段であり、Raspberry Pi 5の標準設定ではありません。実機では、まず内部コンポジタを有効にした状態でV3D、GBM、EGL、映像、タッチ、音声、固定配置を確認してください。

WaydroidとOsmAndの導入手順、トラブルシュート、LIVIの詳細な配置仕様は、[ソフトウェア設計書](docs/SW設計/README.md)および[付録1](docs/SW設計/付録1_RaspberryPi5_Waydroid_OsmAnd導入手順.md)を参照してください。

## テスト

Pythonの仮想環境を有効にした状態で、プロジェクトルートから実行します。

```bash
python -m unittest discover -s tests -v
```

LIVIのウィンドウ配置、ホーム画面との往復、エラー表示、UI描画を重点的に確認できます。

## 開発方針

- Raspberry Pi 5実機ではUIとWaydroidを同じWaylandコンポジタ上で動かします。
- ナビ表示領域は固定し、Swayまたは対応するWayland環境でOsmAndとLIVIを配置します。
- UI上のエラーは、対象サービス・操作・原因を区別して表示します。
- 詳細な仕様変更は、実装と対応する設計書を同時に更新します。
