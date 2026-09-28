# 付録1 Raspberry Pi 5へのWaydroid + OsmAnd導入手順

[ソフトウェア設計図へ戻る](ソフトウェア設計図.md#sec-49)

## 目次
- [1. 目的・適用範囲](#app1-1)
- [2. OS・カーネルの事前確認](#app1-2)
- [3. Waydroidのインストール](#app1-3)
- [4. Androidの初期化・起動](#app1-4)
- [5. GPU描画・表示の確認](#app1-5)
- [6. OsmAndのインストール](#app1-6)
- [7. オフライン地図・音声の準備](#app1-7)
- [8. GPS・現在地補正との接続](#app1-8)
- [9. 通常起動・停止と自動起動方針](#app1-9)
  - [9.1 Debian 13 VirtualBoxでのUI連携試験](#app1-91)
  - [9.2 Pi 5実機でのLIVIネイティブ連携](#app1-92)
- [10. 障害調査・復旧](#app1-10)
  - [10.4 PSI無効によるBinderサービス停止](#app1-104)
  - [10.5 QMLで`appState`がnullになる場合](#app1-qml-appstate)
- [11. 実機確認記録](#app1-11)

<a id="app1-1"></a>
## 1. 目的・適用範囲

Raspberry Pi 5実機にWaydroidを導入し、そのAndroid環境でOsmAndによるオフライン地図表示・経路探索を行うための手順を示す。

**本書は導入・検証手順であり、全手順の実機検証は未完了である。** 確認日: 2026-09-12。PSI有効化後のWaydroid起動成功はユーザー報告により確認した。詳細は[10.4 PSI無効によるBinderサービス停止](#app1-104)に記載する。GPU描画、OsmAnd、GPS連携などの動作確認は別に判定する。

| 項目 | 本手順の対象・方針 |
| --- | --- |
| 装置 | Raspberry Pi 5。VirtualBox内のDebianではない |
| OS | Raspberry Pi OS 64-bit、Debian 13 / Trixieベース、デスクトップ環境付きのOSを対象とする |
| 表示 | Pi 5に接続した画面上のWaylandデスクトップ。X11やSSH端末だけの状態は対象外 |
| Android | WaydroidのARM64イメージ、VANILLAイメージを初期候補とする |
| ナビアプリ | F-Droid配布のOsmAnd~、ARM64向けパッケージを初期候補とする |
| 描画 | Pi 5のGPUによる描画を検証する。VMで使用したSwiftShader強制設定は転用しない |
| ストレージ | 本設計ではSSD。OS、Android、地図、録画の容量を合算し、空き容量を確保する |
| 位置情報 | GPS受信機とLinux・Android間の位置情報連携は別途必要。導入だけでは現在地を取得できない |

コマンドは、特記しない限りPi 5のLinux端末で上から順に実行する。途中でエラーが出た場合は先へ進まない。`waydroid session`、`waydroid app`、`waydroid prop`、画面表示はデスクトップにログインした一般ユーザーで実行し、`sudo`は記載した箇所だけに付ける。

既存SSDのバックアップを取得してから作業する。車載電源管理、録画、GPIO制御を稼働させたままOS更新・再起動を行わない。導入・操作確認は停車中または机上で行う。

<a id="app1-2"></a>
## 2. OS・カーネルの事前確認

### 2.1 OS・アーキテクチャ・Wayland

```bash
cat /etc/os-release
dpkg --print-architecture
uname -r
getconf PAGESIZE
echo "$XDG_SESSION_TYPE"
echo "$WAYLAND_DISPLAY"
df -h / "$HOME"
```

| 確認結果 | 次の対応 |
| --- | --- |
| `VERSION_CODENAME=trixie`、`arm64` | 本書のAPT手順の対象 |
| Bookwormなど別のリリース、または`armhf` | このまま進めない。Trixie用リポジトリを混在させず、対象OSを選び直す |
| `XDG_SESSION_TYPE=wayland`、`WAYLAND_DISPLAY`が空でない | Waylandデスクトップ上で作業を継続 |
| `x11`、`tty`または空欄 | Pi 5のローカルWaylandデスクトップにログインし直す。環境変数を手動設定するだけでは切り替わらない |

Waylandの選択は必要に応じて`sudo raspi-config`の表示関連設定で行い、再ログインまたは再起動後に上記を再確認する。OS Liteにデスクトップを追加する作業は本書の対象外とする。[Raspberry Pi設定資料](https://www.raspberrypi.com/documentation/computers/configuration.html)、[Waydroid導入条件](https://docs.waydro.id/usage/install-on-desktops)

### 2.2 パッケージ更新

OS・データのバックアップ完了後に実行する。更新対象にカーネルが含まれる可能性があるため、再起動後のカーネルを以後の確認対象とする。

```bash
sudo apt update
sudo apt full-upgrade
sudo apt install ca-certificates debian-archive-keyring curl mesa-utils
sudo reboot
```

### 2.3 Pi 5のページサイズ

Pi 5の標準カーネル`kernel_2712.img`には16KBページサイズなどの最適化がある。本手順ではAndroidイメージやネイティブライブラリとの互換性を優先し、**4KBページサイズを検証の初期条件**とする。16KBでは一律に動作不可能という意味ではなく、16KBを採用する場合はAndroidイメージとAPK双方の対応を別途確認する。[Raspberry Piカーネル指定](https://www.raspberrypi.com/documentation/computers/config_txt.html#kernel)、[Androidのページサイズ対応](https://developer.android.com/guide/practices/page-sizes)

`getconf PAGESIZE`が`4096`なら、この小節の変更は不要。`16384`の場合は、標準の共通64-bitカーネルが存在するか確認する。

```bash
ls -l /boot/firmware/kernel8.img
sudo cp -an /boot/firmware/config.txt /boot/firmware/config.txt.before-waydroid
sudo nano /boot/firmware/config.txt
```

`kernel8.img`が存在しない場合や独自カーネルを使用している場合は中止し、対応するカーネル・モジュール・initramfsを確認する。存在する場合は、既存の条件セクションを確認し、Pi 5に有効な`[all]`セクションで次を設定する。同じ装置に有効な`kernel=`設定を重複させない。

```ini
kernel=kernel8.img
```

既存の電源ボタン、GPIO、表示、`auto_initramfs`などの設定は削除しない。nanoでは`Ctrl + O`、`Enter`で保存し、`Ctrl + X`で終了する。

```bash
sudo reboot
```

再ログイン後に確認する。

```bash
uname -r
getconf PAGESIZE
```

`4096`であることを確認する。カーネルの切替だけではBinder対応は追加されないため、次の確認も必須とする。

### 2.4 Binder・memfd

WaydroidはホストカーネルのBinder、およびashmemまたはmemfdを必要とする。本設計ではmemfdを基本とする。[Waydroidカーネル要件](https://docs.waydro.id/debugging/getting-essential-information)

```bash
if [ -r /proc/config.gz ]; then
    zgrep -i -e android -e memfd -e ashmem /proc/config.gz
elif [ -r "/boot/config-$(uname -r)" ]; then
    grep -i -e android -e memfd -e ashmem "/boot/config-$(uname -r)"
else
    echo "Kernel config not found; verify the installed kernel configuration before continuing."
fi
```

設定ファイルが見つからない場合は`sudo modprobe configs`で公開できる場合がある。その後、上記を再実行する。これも失敗した場合は、インストール済みカーネルと一致するビルド設定を入手して確認する。ファイルがないことと機能がないことを混同しない。

| 確認対象 | 判定・対応 |
| --- | --- |
| `CONFIG_ANDROID_BINDER_IPC=y` | カーネル組み込み。モジュールのロードは不要 |
| `CONFIG_ANDROID_BINDER_IPC=m` | 対応する`binder_linux`モジュールが必要 |
| Binder IPC未設定 | APTでWaydroidを入れるだけでは解決しない。Binder対応カーネルの準備が必要 |
| `CONFIG_ANDROID_BINDERFS=y` | binderfs対応。実際のノード構成はWaydroid初期化後にも確認する |
| `CONFIG_MEMFD_CREATE=y` | 本手順で用いる共有メモリ機能を確認 |

Binderがモジュールの場合だけ、次を実行する。

```bash
sudo modprobe binder_linux
```

`Module binder_linux not found`の場合はここで停止する。古いanbox用DKMS、他機種のカーネル、Debian汎用カーネルを無条件に追加しない。Pi 5の表示、SSD起動、カメラ、GPIOと両立するBinder対応カーネルの選定・ビルドは別途実機検証が必要である。[Raspberry Piカーネル資料](https://www.raspberrypi.com/documentation/computers/linux_kernel.html)

**標準Raspberry Pi OSなら必ずBinder対応済みとは扱わない。ここまでの条件が確認できた場合に限り、以降へ進む。**

### 2.5 PSIの確認

Binder・memfdに加え、メモリなどの資源不足による待ち時間を公開するPSI（Pressure Stall Information）が動作していることを確認する。

```bash
cat /proc/pressure/memory
```

`some avg10=...`、`full avg10=...`などの数値が表示されれば、メモリのPSI情報を取得できている。ファイルがない、または読み出せない場合は、[10.4の確認・有効化手順](#app1-104)を実施してからWaydroidの起動へ進む。`CONFIG_PSI=y`でも、起動時に無効化されている場合がある。[Waydroid公式のPSI対処](https://docs.waydro.id/debugging/troubleshooting#warning-service-manager-dev-binder-has-died)

<a id="app1-3"></a>
## 3. Waydroidのインストール

TrixieではDebian Backportsを使用する。2026-09-12時点の掲載バージョンは`1.6.3+ds-2~bpo13+1`。実際のインストールするバージョンはAPTの候補を確認して記録する。Waydroid公式の第三者リポジトリを重ねて追加しない。[Waydroid配布方針](https://docs.waydro.id/usage/install-on-desktops)、[Debianパッケージ](https://packages.debian.org/trixie-backports/waydroid)

まず既存登録を確認する。

```bash
grep -Rns 'trixie-backports' /etc/apt/sources.list /etc/apt/sources.list.d
```

`/etc/apt/sources.list`がないという表示だけなら、`.sources`ファイル側の結果を確認する。同じ登録がある場合は新規追加を省略する。未登録の場合だけ、以下のファイルを編集する。

```bash
sudo nano /etc/apt/sources.list.d/debian-backports.sources
```

次を記載する。[Debian Backports登録手順](https://backports.debian.org/Instructions/)

```text
Types: deb
URIs: https://deb.debian.org/debian
Suites: trixie-backports
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
```

保存後、依存関係を事前確認してから導入する。

```bash
sudo apt update
apt-cache policy waydroid
sudo apt install --simulate -t trixie-backports waydroid
```

候補がない、署名エラーが出る、Pi用の重要パッケージが削除されるなどの場合は中止する。問題がない場合に実行する。

```bash
sudo apt install -t trixie-backports waydroid
waydroid --version
```

<a id="app1-4"></a>
## 4. Androidの初期化・起動

### 4.1 初回初期化

インターネット接続中に実行する。Google Playを含まないVANILLAイメージとし、ARM64ホストに対応するイメージを取得する。VMで用いたx86_64イメージをコピーしない。

```bash
sudo waydroid init -s VANILLA -c https://ota.waydro.id/system -v https://ota.waydro.id/vendor
```

これは初回導入用である。初期化済みの場合は`-f`で強制再初期化せず、既存設定・イメージを確認する。OTAと初期化オプションは[公式導入資料](https://docs.waydro.id/usage/install-on-desktops)および[CLI資料](https://docs.waydro.id/usage/waydroid-command-line-options)を参照する。

```bash
sudo grep -nE '^arch|^vendor_type|^images_path|^ro.hardware' /var/lib/waydroid/waydroid.cfg
sudo cp -an /var/lib/waydroid/waydroid.cfg /var/lib/waydroid/waydroid.cfg.before-pi5-tuning
sudo systemctl enable --now waydroid-container
```

`arch`がARM64用であることを確認する。`ro.hardware.egl=swiftshader`や`ro.hardware.gralloc=default`を新規追加しない。Pi 5向けの初回検証はGPUの自動検出を使用する。

### 4.2 デスクトップから起動

Pi 5のWaylandデスクトップ上で、一般ユーザーとして実行する。

```bash
waydroid session start
```

`Android with user 0 is ready`を確認した後、別の端末で実行する。

```bash
waydroid show-full-ui
```

待機しても準備完了にならない、画面が出ない場合は[10. 障害調査・復旧](#app1-10)へ進む。再インストールや設定の大量追加を先に行わない。

<a id="app1-5"></a>
## 5. GPU描画・表示の確認

```bash
ls -l /dev/dri
glxinfo -B
sudo waydroid shell getprop ro.product.cpu.abilist
sudo waydroid shell getprop sys.boot_completed
sudo waydroid shell getprop ro.hardware.egl
sudo waydroid shell -- dumpsys SurfaceFlinger | grep -iE 'GLES|renderer|V3D|SwiftShader|llvmpipe'
```

| 確認対象 | 合格の目安 |
| --- | --- |
| Android ABI | `arm64-v8a`を含む |
| Android起動 | `sys.boot_completed`が`1` |
| 描画 | Pi 5のV3D系レンダラーを確認。SwiftShaderやllvmpipeによるCPU描画ではない |
| 画面 | 設定アプリが見え、タッチやドラッグに応答する |
| 安定性 | 画面が固まらず、SystemUIの再起動や描画エラーが繰り返されない |

`glxinfo`はホスト側の確認であり、Xwaylandがない環境では使えない場合もある。Android側の描画結果とは分けて判定する。`ro.hardware.egl=mesa`だけでGPU動作成功とは判定しない。VMで表示された`SVGA3D`はPi 5実機の合格基準ではない。

まず設定アプリで確認する。

```bash
waydroid app launch com.android.settings
```

個別のアプリウィンドウとして表示したい場合は、セッション稼働中に次を実行する。

```bash
waydroid prop set persist.waydroid.multi_windows true
waydroid session stop
waydroid app launch com.android.settings
```

元の表示方式へ戻す場合は`true`を`false`にして再度セッションを停止し、`waydroid show-full-ui`を実行する。ウィンドウの移動操作は採用Waylandコンポジタに従う。[Waydroid表示設定](https://docs.waydro.id/usage/waydroid-prop-options)

<a id="app1-6"></a>
## 6. OsmAndのインストール

Pi 5のブラウザで[F-DroidのOsmAnd~配布ページ](https://f-droid.org/en/packages/net.osmand.plus/)を開く。2026-09-12時点の推奨掲載バージョンは`5.3.10`、ARM64向けパッケージのversionCodeは`531003`。これは初期評価候補であり、常に最新バージョンという意味ではない。

| 項目 | 選択内容 |
| --- | --- |
| 配布元 | F-Droid |
| アプリ | OsmAnd~ |
| アーキテクチャ | `arm64-v8a`。`x86` / `x86_64`向けパッケージを選ばない |
| パッケージ名 | `net.osmand.plus` |
| 初期候補 | 5.3.10 / 531003。以後の更新は実機試験後に採用 |

該当するARM64向けパッケージの「Download APK」から取得する。保存先はLinuxユーザーの`$HOME/Downloads`とし、以下はファイル名が`net.osmand.plus_531003.apk`の場合の例である。ブラウザが別の場所・名前で保存した場合は、実ファイルのパスに置き換える。配布が終了している場合は同ページのARM64向けの安定バージョン候補を選び、バージョンを記録する。

```bash
ls -lh "$HOME/Downloads/net.osmand.plus_531003.apk"
sha256sum "$HOME/Downloads/net.osmand.plus_531003.apk"
waydroid app install "$HOME/Downloads/net.osmand.plus_531003.apk"
waydroid app launch net.osmand.plus
```

Waydroid起動済みの状態で実行する。SHA-256は取得ファイルの識別記録であり、計算するだけで配布元の真正性が証明されるわけではない。配布ページの署名情報も確認する。F-Droid配布パッケージと別ストアの配布パッケージは同じパッケージ名でも署名が異なる場合があるため、更新元を統一し、署名不一致時にデータを消して解決しない。[WaydroidのAPK導入方法](https://docs.waydro.id/usage/install-and-run-android-applications)

<a id="app1-7"></a>
## 7. オフライン地図・音声の準備

1. OsmAndを起動し、初期設定と言語設定を行う。
2. 「地図とリソース」などのダウンロード画面から、日本の走行予定地域と経路上の地域の標準地図を取得する。表示名・地域分割はバージョンにより異なる。
3. 自動車プロファイルを選び、経路探索をオフライン方式にする。必要な地図のダウンロード完了を確認する。
4. 日本語の案内音声を設定する。TTS方式を使用する場合は、Android側の対応TTSエンジンとオフライン日本語音声も準備する。VANILLAイメージにGoogleの音声エンジンがあるとは前提にしない。
5. Pi 5のインターネット接続を切り、保存地域の地図閲覧、検索、手動指定した出発地点から目的地までの経路探索を確認する。
6. 音声の試聴・案内テストを行い、ネットワークなしでUSB DACから日本語案内が聞こえることを確認する。利用できなければ音声案内は未完了と記録する。

オンライン地図の表示履歴だけではオフライン利用の確認にならない。最初は必要地域だけを取得し、録画用SSDの容量を圧迫しないようにする。[OsmAnd地図ダウンロード](https://osmand.net/docs/user/start-with/download-maps/)、[OsmAnd経路設定](https://osmand.net/docs/user/navigation/setup/route-navigation/)

地図と設定はOsmAndのエクスポート機能などでバックアップする。WaydroidのAndroidデータ、OsmAnd地図・設定は録画アップロード後の削除対象に含めない。

<a id="app1-8"></a>
## 8. GPS・現在地補正との接続

**ここまでの手順で確認するのは地図表示・経路探索であり、実車での現在地追従は別の実装項目である。** Pi 5本体を起動しただけではGNSS位置を取得できず、Linux側のgpsd情報もこの導入手順だけではOsmAndへ渡らない。

本設計の接続順序は次のとおりとする。

```text
GPS受信機 -> gpsd -> 現在地補正サービス
                       ^
                       |
                  OBD2 / IMU

現在地補正サービス
  -> Linux側の位置情報連携サービス
  -> Android位置情報連携アプリ
  -> Android位置情報サービス
  -> OsmAnd
```

Android側のモック位置情報方式、権限、受信周期、有効期限、精度情報の反映はT.B.Dである。地図が表示されたことをGPS連携完了とは扱わない。[SW設計4.9](ソフトウェア設計図.md#sec-49)、[SW設計4.10](ソフトウェア設計図.md#sec-410)を参照する。

<a id="app1-9"></a>
## 9. 通常起動・停止と自動起動方針

初回導入と動作確認が完了した後の通常起動例を示す。

```bash
sudo systemctl start waydroid-container
waydroid app launch net.osmand.plus
```

事前に同じ一般ユーザーのWaylandデスクトップへログインしておく。`app launch`は必要に応じてセッションを起動するため、初回のイメージ取得やAPKインストールを毎回実行する必要はない。

停止時は、OsmAnd内で案内・設定変更を終えてから次を実行する。

```bash
waydroid session stop
sudo systemctl stop waydroid-container
```

`systemctl enable`で有効にしたのはコンテナ管理サービスであり、デスクトップへのログインやOsmAnd画面の自動表示まで保証するものではない。車載自動起動は、Waylandログイン後に一般ユーザーの自動起動処理から起動する方式を別途実装する。rootの起動スクリプトから無条件にGUIを起動しない。

UI・カメラ・電源管理をWaydroidの起動待ちで止めない。Picoからの終了要求時の案内停止、保存確認、タイムアウト付き停止は[SW設計9章](ソフトウェア設計図.md#sec-9)で管理する。

<a id="app1-91"></a>
### 9.1 Debian 13 VirtualBoxでのUI連携試験

この節は、Raspberry Pi 5実機へ導入する前に、Debian 13のVirtualBox環境で自作UI、JSON通信、Waydroidナビ管理、OsmAndの起動経路を確認するための手順である。LIVIはWaydroid APKではなく、Pi 5上のネイティブプロセスとして別サービスから起動する。GPIO、車体信号、実カメラ、Pi 5のGPU性能は検証対象外とする。

#### 9.1.1 依存パッケージ

WaydroidのCLIはシステムPythonで`dbus`を読み込むため、仮想環境だけにPythonパッケージを導入しても解決しない。Debianパッケージを先に導入する。

```bash
sudo apt update
sudo apt install python3-dbus python3-gi gir1.2-glib-2.0 python3-venv
```

確認する。

```bash
/usr/bin/python3 -c "import dbus; print('dbus ok')"
```

#### 9.1.2 ソースとPython環境

リポジトリのルートを`~/codex`とする。`src/raspberry_pi5`だけを単独で移動せず、`src`以下の構成を保ったまま配置する。

```bash
cd ~/codex
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r src/raspberry_pi5/requirements-ui.txt
export PYTHONPATH="$PWD/src/raspberry_pi5"
```

#### 9.1.3 WaylandとWaydroid

VirtualBoxのDebianデスクトップへ一般ユーザーでログインし、同じ端末で次を確認する。

```bash
echo "$XDG_CURRENT_DESKTOP"
echo "$XDG_SESSION_DESKTOP"
echo "$WAYLAND_DISPLAY"
```

`WAYLAND_DISPLAY`が空の場合は、Waylandセッションへログインし直す。Waydroidのコンテナとセッションを起動する。

```bash
sudo systemctl start waydroid-container
waydroid session start
waydroid status
```

#### 9.1.4 Androidアプリの確認

OsmAndまたはLIVIのAPKをWaydroidへインストールした後、必ず一覧から実際のパッケージ名を確認する。`<OsmAndのパッケージ名>`のような説明用の山括弧をそのままシェルへ入力しない。

```bash
waydroid app list
waydroid app launch 実際のパッケージ名
```

手動起動でAndroidアプリのウィンドウが表示されることを確認してから、自作UIとの連携試験へ進む。パッケージ名、APKの配布元、バージョン、署名を試験記録へ残す。

#### 9.1.5 管理プログラムとUI

仮想環境では、コンテナを手動で起動してから管理プログラムを起動する。まず設定ファイルの`packages`と`commands`へ、`waydroid app list`で確認したパッケージ名を記載する。管理プログラムは常駐するため、起動した端末のプロンプトは戻らない。

```bash
cd ~/codex
source .venv/bin/activate
export PYTHONPATH="$PWD/src/raspberry_pi5"

python src/raspberry_pi5/waydroid_navigation_manager.py \
  --config ~/.config/l880k-car-navigation/navigation.json \
  --socket /tmp/l880k-navigation.sock
```

別の端末でUIを起動する。

```bash
cd ~/codex
source .venv/bin/activate
export PYTHONPATH="$PWD/src/raspberry_pi5"
export L880K_NAV_SOCKET=/tmp/l880k-navigation.sock
export QT_QPA_PLATFORM=wayland

python src/raspberry_pi5/main_app.py
```

UIのナビ画面でOsmAndを選択すると、`02 Waydroidナビ管理`へJSON通信を送る。LIVIを選択した場合は、Waydroid管理へ送らず`11 LIVI連携`の専用ソケットへ送る。LIVIのウィンドウはSway上でPIDを確認して固定表示領域へ配置する。

#### 9.1.6 仮想環境で確認できないもの

以下はDebian 13 VirtualBoxでは合否判定しない。

- Raspberry Pi 5のGPU性能
- GPIO、ACC、バックギア、Picoとの通信
- MIPIカメラ、USBカメラの車載構成
- Pi 5の電源断・起動制御
- Waylandコンポジタ固有の外部ウィンドウ前面化
- 車載ディスプレイのタッチ入力と解像度

#### 9.1.7 起動スクリプトによる簡易試験

上記の準備を毎回手入力せずに実行する場合は、リポジトリの`scripts/start_debian13_ui_test.sh`を使用する。Waydroid CLIはシステムPython、UIは`.venv`または`.venv-waydroid`で動かし、`dbus`依存があるWaydroid CLIをUI用仮想環境から実行しない構成にしている。プロジェクト内にない場合は、`$HOME/.venv`と`$HOME/.venv-waydroid`も自動検索する。Debian 13のテスト設定もSwayを使用し、実機と同じ固定領域配置を確認する。

```bash
cd ~/codex
chmod +x scripts/start_debian13_ui_test.sh
./scripts/start_debian13_ui_test.sh
```

テストを終了する場合は、起動した端末を強制終了せず、別の端末から停止スクリプトを実行する。停止スクリプトはPIDとコマンドラインを確認してからUI、ナビ管理、テスト起動したWaydroidセッション、コンテナを順に停止し、残ったIPCソケットを削除する。

```bash
cd ~/codex-latest
chmod +x scripts/stop_debian13_ui_test.sh
./scripts/stop_debian13_ui_test.sh
```

起動端末を閉じてPID記録が残っている場合も、同じ停止スクリプトを実行する。別の用途で起動していたWaydroidセッションやコンテナは、起動状態を記録していないため停止しない。

使用する仮想環境を明示する場合は、次のように指定する。

```bash
L880K_UI_VENV="$HOME/codex/.venv-waydroid" ./scripts/start_debian13_ui_test.sh
```

仮想環境がまだない場合は、次のように作成する。

```bash
cd ~/codex
python3 -m venv .venv-waydroid
source .venv-waydroid/bin/activate
python -m pip install -r src/raspberry_pi5/requirements-ui.txt
```

このスクリプトは、コンテナ起動、Waydroidセッション確認、Androidの起動完了確認、管理プログラムの常駐起動、`/tmp/l880k-navigation.sock`の作成確認、UI起動を順番に行う。`Session: RUNNING`はセッション開始の確認に過ぎないため、`sudo waydroid shell getprop sys.boot_completed`の結果が`1`になるまでUIを起動しない。Android起動完了の待ち時間は既定120秒で、`L880K_ANDROID_BOOT_TIMEOUT=180`のように変更できる。管理プログラムのログは`/tmp/l880k-navigation-manager.log`、Waydroidセッションのログは`/tmp/l880k-waydroid-session.log`へ保存する。UIを終了すると管理プログラムも終了する。

OsmAndのパッケージ名は`net.osmand.plus`としている。別の配布形態を使用する場合は`scripts/debian13-navigation-test.json`の`packages.navigation`と`commands.navigation`を、`waydroid app list`で確認した値へ変更する。LIVIの設定はWaydroid設定へ追加せず、`src/raspberry_pi5/config/livi.example.json`をコピーして実行ファイルを指定する。

<a id="app1-92"></a>
### 9.2 Pi 5実機でのLIVIネイティブ連携

LIVIはWaydroid内へ入れず、Pi 5のSway Waylandセッションでネイティブプロセスとして動かす。公式リポジトリの対象バージョン・依存関係・接続方式を確認してから導入する。公式のビルド例は[LIVI公式リポジトリ](https://github.com/f-io/LIVI)を参照する。

```bash
cd /opt
sudo git clone --branch main https://github.com/f-io/LIVI.git livi
cd /opt/livi
corepack enable
pnpm run install:ci
pnpm run build:linux:arm64
```

上記は公式ビルド手順の確認例であり、対象コミット、依存パッケージ、インストール先は試験記録へ残す。ビルドで生成された実行ファイルの場所を確認し、設定例を実機用設定へコピーする。

```bash
mkdir -p ~/.config/l880k-car-navigation
cp src/raspberry_pi5/config/livi.example.json ~/.config/l880k-car-navigation/livi.json
nano ~/.config/l880k-car-navigation/livi.json
```

DebianパッケージでインストールしたLIVIは、通常`/opt/LIVI/livi`に配置される。その場合は`executable`に`/opt/LIVI/livi`、`working_directory`に`/opt/LIVI`を設定する。別の方法でインストールした場合は、`command -v livi`と`readlink -f $(command -v livi)`で実際の実行ファイルを確認してから設定する。`args`へ未確認のオプションを追加せず、LIVI公式の採用バージョンで確認した引数だけを固定文字列として記載する。電話接続状態はLIVIの正式なAPIが確認できるまでUI上でUNKNOWNと表示し、プロセス起動だけで接続済みとは扱わない。

systemdへ登録する場合は、サービスファイルを一般ユーザーのWaylandセッションで有効化する。`ExecStart`のソースルートとPython実行環境は実機へ配置した場所に合わせる。サービスはユーザー単位で動作し、rootのGUIセッションを作らない。

```bash
mkdir -p ~/.config/systemd/user
cp src/raspberry_pi5/systemd/l880k-livi.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now l880k-livi.service
systemctl --user status l880k-livi.service --no-pager
```

UI設定にはLIVIソケットを指定する。OsmAndは従来どおりWaydroid管理ソケット、LIVIは別ソケットとする。

```json
{
  "navigation_socket_path": "/run/user/1000/l880k-navigation.sock",
  "livi_socket_path": "/run/user/1000/l880k-livi.sock",
  "navigation_viewport": {"x": 0, "y": 62, "width": 1280, "height": 580}
}
```

既存の`~/.config/l880k-car-navigation/ui.json`に旧座標が保存されている場合、標準1280×720画面では`navigation_viewport`だけを上記へ更新する。他の設定項目は保持する。上部62pxと下部78pxは自作UIに残し、LIVIのサイズを変更した後に最終座標へ移動する。

受入条件は、LIVIのプロセスが固定引数で起動すること、SwayのPID一致ウィンドウだけが指定領域へ移動すること、OsmAndのWaydroidセッションがLIVI選択時に起動されないこと、LIVI停止時に他者が起動した同名プロセスを終了しないことである。ナビ→ホーム→ナビを繰り返して同じLIVIが再表示されること、OsmAndまたはLIVI表示中の下部ナビ再押下と、OsmAnd・LIVI・Waydroidホーム表示中の上部バー操作で画面が隠れないこと、上下の自作UIバーを覆わないことも確認する。バー操作後は自作UIのクリック処理完了を待ってから対象を前面へ戻すため、`external_focus_delay_ms`の既定値40msを使用する。WaydroidボタンではOsmAnd表示中でもAndroidホームへ切り替わることを確認する。

Debian 13仮想環境でLIVI表示直後に`llvmpipe`の`segfault`が記録された場合、原因切り分けとして次を実行できる。

```bash
cd ~/codex-latest
./scripts/stop_debian13_ui_test.sh
pkill -TERM -u "$(id -u)" -x livi || true
sleep 3
LIVI_NO_COMPOSITOR=1 DEBUG=1 ./scripts/start_debian13_sway_ui_test.sh
```

`LIVI_NO_COMPOSITOR=1`は、LIVI内部の画面合成処理を無効にし、外側のSwayへLIVIの画面を直接配置する診断設定である。自作UI側のSwayとLIVI連携サービスは引き続き使用する。この設定で表示が継続した場合、LIVI本体全体ではなく、VirtualBoxのCPU描画（`llvmpipe`）と内部コンポジタの組み合わせが原因候補となる。ただし、Android Auto・CarPlay映像、タッチ入力、音声入出力まで正常とは限らないため、表示確認だけで実機採用を決めない。

Raspberry Pi 5実機では、まず`LIVI_NO_COMPOSITOR`を設定せずに起動し、V3D・GBM・EGLの実描画経路で内部コンポジタが安定するか確認する。無効化設定を実機で使う場合は、映像・タッチ・音声・Sway固定配置を個別に確認してから採用する。[LIVI公式診断手順](https://github.com/f-io/LIVI/blob/main/DEBUGGING.md)

<a id="app1-10"></a>
## 10. 障害調査・復旧

### 10.1 設定変更前にログを取得

```bash
waydroid status
sudo systemctl status waydroid-container --no-pager
sudo journalctl -u waydroid-container -b -n 100 --no-pager
sudo tail -n 100 /var/lib/waydroid/waydroid.log
sudo waydroid shell getprop sys.boot_completed
sudo waydroid shell -- logcat -d -b crash
sudo waydroid shell -- logcat -d -b all | tail -n 200
```

Androidが起動していない場合、`shell`によるログ取得も失敗する。その場合はsystemdのログを優先する。ログを共有する前に位置情報、認証情報、ユーザー名などの不要な個人情報を確認する。

`shell --`の区切りを省略すると、`-d`や`-b`がWaydroid自身のオプションと解釈されてエラーになる。最初からgrepで限定すると例外の先頭や原因が欠落するため、上記はフィルタなしのログを使用する。[Waydroid CLI](https://docs.waydro.id/usage/waydroid-command-line-options)、[引数処理の実装](https://github.com/waydroid/waydroid/blob/main/tools/helpers/arguments.py)

| 症状 | 確認・対応 |
| --- | --- |
| Binderが見つからない、サービスが起動しない | 現在動作中のカーネルのBinder・memfd対応とモジュールを再確認する |
| `Service manager /dev/binder has died`、起動後すぐにコンテナが停止 | [10.4](#app1-104)のPSI確認を行う。このメッセージだけでBinderドライバー不良と断定しない |
| `WAYLAND_DISPLAY`関連エラー | ローカルWaylandデスクトップとログインユーザーを確認する |
| `mesa`だが画面が出ない | GPU描画成功とは扱わない。起動完了、SystemUIのクラッシュ、描画バッファと表示経路のログを確認する |
| `failed to get driver name for fd -1` | デバイス情報の取得失敗。これだけで権限不足と断定せず、前後のログとGPUノードを確認する |
| `ModuleNotFoundError: No module named 'dbus'` | 仮想環境ではなくシステムPythonへ`python3-dbus`、`python3-gi`、`gir1.2-glib-2.0`をAPTで導入し、`/usr/bin/python3 -c "import dbus"`で確認する |
| `予期しないトークン newline` | `<OsmAndのパッケージ名>`などの説明用文字をそのまま入力している。`waydroid app list`の実際のパッケージ名へ置き換える |
| UIに仮の地図だけが表示される | `NavigationScreen.qml`は画面確認用の仮表示である。Waydroidアプリの起動と外部ウィンドウの前面化は別試験であり、`show_window: true`だけでは前面化を確認できない |
| 広色域・`101010-2`関連のエラー | その行だけで表示不能の主因とは断定しない。クラッシュログと標準色形式へのフォールバック結果を確認する |
| SwiftShader / llvmpipeで遅い | CPU描画。VM設定の流用、GPU対応、カーネル・イメージの組み合わせを確認する |
| APKインストール時のABI不一致 | ARM64用APKとAndroid ABIを確認する |
| 現在地が出ない | アプリ権限だけでなく、8章のGPS連携が未実装でないか確認する |

`chmod 777 /dev/dri/*`、無条件のセキュリティ機能無効化、データディレクトリの削除を通常の復旧手順にしない。

### 10.2 Waydroid設定の復旧

4章で作成したバックアップが存在することを確認してから実行する。戻るのはその時点の設定であり、「必ず表示できる設定」を保証するものではない。

```bash
ls -l /var/lib/waydroid/waydroid.cfg.before-pi5-tuning
waydroid session stop
sudo systemctl stop waydroid-container
sudo cp -a /var/lib/waydroid/waydroid.cfg.before-pi5-tuning /var/lib/waydroid/waydroid.cfg
sudo waydroid upgrade -o
sudo systemctl start waydroid-container
waydroid show-full-ui
```

`upgrade -o`は設定の再生成に用いる。Androidデータの初期化やAPKの再インストールは行わない。通常の`upgrade`によるイメージ更新とは区別する。

### 10.3 カーネル指定の復旧

ページサイズ変更後に問題が起きた場合は、2.3で編集した`kernel=`設定を元に戻す。それ以降に他の設定を変更していなければ、バックアップからの復元も可能である。

```bash
sudo cp -a /boot/firmware/config.txt.before-waydroid /boot/firmware/config.txt
sudo reboot
```

Pi 5が起動しない場合は電源を切り、起動媒体のbootパーティションを別PCで開いて`config.txt`を復元する。バックアップがない場合や独自カーネルの場合は、上記を実行せず変更箇所を確認する。

<a id="app1-104"></a>
### 10.4 PSI無効によるBinderサービス停止

#### 発生した症状と確認結果

2026-09-12、Pi 5でWaydroidを起動した際、次のメッセージが出てAndroidを利用できなかった。

```text
Service manager /dev/binder has died
Waiting for Binder service manager...
Service manager never appeared
Failed to access IPlatform service
```

詳細ログでは、Androidコンテナが一度`RUNNING`になり、約16秒後に終了して`STOPPED`になる動作を確認した。停止時には`Failed to run lxc.hook.post-stop`と終了コード`126`も記録されたが、これは停止後の処理のエラーであり、単独では最初の停止原因を示さない。systemdの`Started waydroid-container.service`もAndroidの起動完了とは区別する。

| 確認項目 | 当該環境での結果 |
| --- | --- |
| カーネルのページサイズ | `4096`。16KBページの環境ではなかった |
| Binder / memfd | `CONFIG_ANDROID_BINDER_IPC=y`、`CONFIG_ANDROID_BINDERFS=y`、`CONFIG_MEMFD_CREATE=y` |
| Binderデバイス | `/dev/binder`とbinderfsのノードが存在した |
| PSIのビルド設定 | `CONFIG_PSI=y`、`CONFIG_PSI_DEFAULT_DISABLED=y` |
| 実行中のPSI | `/proc/pressure/memory`が存在しなかった |
| 起動引数 | `/proc/cmdline`に`psi=1`がなかった |
| 対処結果 | `psi=1`を追加して再起動後、Waydroidが起動したとユーザーから報告あり |

当該環境では、PSIに対応するカーネルを使用していたものの、既定でPSIが無効だった。有効化で起動が改善した事例として記録する。同じBinderエラーが常にPSIを原因とするとは扱わない。[Waydroid公式トラブルシュート](https://docs.waydro.id/debugging/troubleshooting#warning-service-manager-dev-binder-has-died)

#### 確認方法

```bash
cat /proc/pressure/memory
grep -E '^CONFIG_PSI|^# CONFIG_PSI' "/boot/config-$(uname -r)"
cat /proc/cmdline
```

カーネル設定ファイルがない場合は、2.4と同様に`/proc/config.gz`など、動作中カーネルの設定を確認する。

`CONFIG_PSI=y`でPSI情報を取得できない場合は起動引数を確認する。既に情報を取得できている場合はPSIを変更せず、10.1のログで別の原因を調べる。`CONFIG_PSI`が未設定の場合は、`psi=1`だけでは機能を追加できないため、対応カーネルが必要となる。

#### 有効化手順

以下は、Pi 5の標準起動引数ファイルが`/boot/firmware/cmdline.txt`で、`CONFIG_PSI=y`の環境を対象とする。

1. 設定をバックアップし、編集する。

```bash
sudo cp -an /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.before-psi
sudo nano /boot/firmware/cmdline.txt
```

2. 既存の長い1行の末尾に半角スペースを挟んで`psi=1`を追加する。既存の内容を保持し、途中に改行を入れない。`config.txt`ではなく`cmdline.txt`を編集する。既存の`psi=0`があれば`psi=1`に置き換え、`psi=`を重複させない。
3. `Ctrl + O`、`Enter`で保存し、`Ctrl + X`で終了する。作業中の内容を保存してから再起動する。

```bash
sudo reboot
```

4. 再ログイン後に、起動引数の反映とPSI情報の取得を確認する。

```bash
cat /proc/cmdline
cat /proc/pressure/memory
```

起動引数に`psi=1`があり、メモリ情報に`some avg10=...`、`full avg10=...`などが表示されることを確認する。数値は負荷によって変わり、ゼロでも異常ではない。[LinuxカーネルのPSI資料](https://docs.kernel.org/accounting/psi.html)

5. Pi 5のWaylandデスクトップ上の端末で起動する。

```bash
sudo systemctl start waydroid-container
waydroid session start
```

`Android with user 0 is ready`が出たら別の端末で画面を開く。

```bash
waydroid show-full-ui
```

この対処後にユーザーからWaydroidの起動成功が報告された。GPU描画やOsmAndの動作確認は5章以降に従って別途実施する。

変更を戻す必要がある場合は、追加した`psi=1`を削除するか、変更前の`psi=`設定へ戻して再起動する。起動できない場合は、起動媒体のbootパーティションを別PCで開き、`cmdline.txt`を修復する。バックアップから復元する場合は、バックアップ後に加えた他の設定を失わないよう差分を確認する。

<a id="app1-qml-appstate"></a>
### 10.5 QMLで`appState`がnullになる場合

QML起動時に次のようなエラーが出る場合は、QMLと`main_app.py`の世代が一致していない可能性が高い。

```text
TypeError: Cannot read property 'screen' of null
TypeError: Cannot read property 'warning' of null
TypeError: Cannot read property 'recording' of null
```

現在のUIは、QMLを読み込む前に`appState`と`screenController`を登録する必要がある。Debian側で次を確認する。

```bash
cd ~/codex
grep -n "setContextProperty" src/raspberry_pi5/main_app.py
grep -n "engine.load" src/raspberry_pi5/main_app.py
```

次の3行が表示され、`engine.load`より前に並んでいれば正しい。

```text
engine.rootContext().setContextProperty("appState", state)
engine.rootContext().setContextProperty("screenController", controller)
engine.load(str(qml_path))
```

表示されない場合は、Debian側のプロジェクトを最新状態へ更新する。リモートリポジトリを設定済みなら、作業内容を確認してから更新する。

```bash
cd ~/codex
git status
git pull --ff-only
```

更新後、実際に起動するファイルが想定したプロジェクト内にあることを確認する。

```bash
pwd
readlink -f src/raspberry_pi5/main_app.py
grep -n "setContextProperty" src/raspberry_pi5/main_app.py
```

その後、実行中のUIを`Ctrl + C`で終了してからテストスクリプトを再実行する。

```bash
./scripts/start_debian13_ui_test.sh
```

この問題はWaydroidのBinderやOsmAndの起動失敗とは別であり、QMLへ公開するPython側の状態オブジェクトが不足していることが原因である。QML側に一時的なnull判定を追加してエラーを隠すのではなく、Python側とQML側を同じコミットの組み合わせで使用する。

### 10.1 ナビ起動コマンドのログ確認

Debian用テスト設定では、Waydroid・セッション・OsmAnd・画面表示コマンドの結果を次のファイルへ記録する。

```bash
tail -n 200 /tmp/l880k-navigation-commands.log
```

`command`、`returncode`、`timed_out`、`stdout`、`stderr`を確認する。`show-full-ui`が起動しない場合は`stderr`を、OsmAndが起動しない場合は`waydroid app launch net.osmand.plus`の`returncode`と`stderr`を確認する。ログが存在しない場合は、テストスクリプトを再起動して最新ソースの管理プログラムを起動する。

<a id="app1-11"></a>
## 11. 実機確認記録

以下を埋めてから採用構成を確定する。2026-09-12時点で、提示ログによるカーネル機能の確認と、ユーザー報告によるPSI有効化後のWaydroid起動成功を記録した。それ以外の項目は未確認である。

| 項目 | 実機記録 / 結果 |
| --- | --- |
| OSイメージ名・リリース | 未確認 |
| カーネルのバージョン・ページサイズ・Binder設定 | カーネルのバージョンは未記録。ページサイズ`4096`、Binder IPC / binderfs / memfdの有効設定を提示ログで確認 |
| PSI・Waydroid起動 | 既定でPSI無効。`psi=1`追加・再起動後に起動成功とのユーザー報告あり（2026-09-12） |
| Waylandコンポジタとバージョン | 未確認 |
| Waydroidパッケージのバージョン | 未確認 |
| Android system / vendorイメージ・ABI | 未確認 |
| SurfaceFlingerのGLESレンダラー | 未確認 |
| OsmAnd配布元・バージョン・versionCode・APK SHA-256 | 未確認 |
| タッチ操作・ウィンドウ移動・UI復帰 | 未確認 |
| オフライン地図表示・検索・経路探索 | 未確認 |
| オフライン日本語音声・USB DAC出力 | 未確認 |
| GPS連携・ロスト時補正 | 未実装 / T.B.D |
| カメラ録画・Mopidy・LIVIとの同時動作 | 未確認 |
| ACC連動起動・正常終了・再起動後の保存状態 | 未確認 |

初期導入の合格と、位置情報連携を含む車載ナビ全体の合格は分ける。GPU描画・安定性・オフライン動作を確認したOS・カーネル・Waydroid・OsmAndの組み合わせを記録し、更新時に再試験する。

<a id="app1-12"></a>
## 12. Raspberry Pi 5実機のネイティブWayland構成

実機ではブラウザ表示を使用せず、PySide6/QML UIとWaydroidを同じSway Waylandセッションで動かす。UIのナビ表示領域は固定座標で管理し、Waydroidナビ管理がSwayへウィンドウの移動・リサイズを依頼する。

### 12.1 Swayの準備

```bash
sudo apt update
sudo apt install sway
mkdir -p ~/.config/sway
cp ~/codex-latest/src/raspberry_pi5/systemd/sway/l880k-car-navigation-sway.conf ~/.config/sway/config
```

ログイン時にGNOMEではなくSwayセッションを選択する。Swayセッション内で次を確認する。

```bash
echo "$XDG_CURRENT_DESKTOP"
echo "$WAYLAND_DISPLAY"
swaymsg -t get_version
```

### 12.2 Pi 5用ナビ設定

```bash
mkdir -p ~/.config/l880k-car-navigation
cp ~/codex-latest/src/raspberry_pi5/config/navigation-pi5-sway.json \
  ~/.config/l880k-car-navigation/navigation.json
sudo bash ~/codex-latest/scripts/install_waydroid_home_sudoers.sh "$USER"
```

最後のコマンドは、管理プログラムがAndroidのHOMEキーを送るために、`/usr/bin/waydroid shell input keyevent KEYCODE_HOME`だけをパスワードなしで実行できるようにする。任意の`waydroid shell`や他のrootコマンドは許可しない。設定内容は`sudo visudo -cf /etc/sudoers.d/l880k-waydroid-home`で検査できる。

管理プログラムはユーザーサービスとして起動し、UIは同じSwayセッションの利用者として起動する。ユニットファイルを配置し、`ExecStart`のプロジェクトパスは実際の配置先へ合わせる。

```bash
mkdir -p ~/.config/systemd/user
cp ~/codex-latest/src/raspberry_pi5/systemd/l880k-navigation.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now l880k-navigation.service
source ~/codex-latest/.venv/bin/activate
export QT_QPA_PLATFORM=wayland
export PYTHONPATH="$HOME/codex-latest/src/raspberry_pi5"
python ~/codex-latest/src/raspberry_pi5/main_app.py
```

ナビを選択すると、WaydroidのウィンドウをSwayのツリーから検出し、設定されたナビ表示領域へ移動・リサイズする。Waydroidの`app_id`やウィンドウクラスが環境によって異なる場合は、`navigation-pi5-sway.json`の`waydroid_window_identifiers`へ実際の識別子を追加する。

### 12.3 Debian 13仮想環境もSway配置で試験する

VirtualBox上のDebian 13でも、GNOMEではなくSwayセッションを使用する。テスト設定は`scripts/debian13-navigation-test.json`で`display_backend`を`sway`にしているため、GNOMEのまま起動するとスクリプトが停止し、Swayへの切り替えを案内する。

```bash
sudo apt update
sudo apt install sway
mkdir -p ~/.config/sway
cp ~/codex-latest/src/raspberry_pi5/systemd/sway/l880k-car-navigation-sway.conf ~/.config/sway/config
```

いったんログアウトし、ログイン画面のセッション選択で`Sway`を選ぶ。Swayへ入った後、次を確認する。

```bash
echo "$XDG_CURRENT_DESKTOP"
echo "$WAYLAND_DISPLAY"
swaymsg -t get_version
```

最後のコマンドがSwayのバージョンを返せば、同じ端末からテストスクリプトを実行できる。GNOMEなど別のGUIセッションで実行した場合も、テストスクリプトは自動的にネストしたSwayを起動する。

```bash
cd ~/codex-latest
./scripts/start_debian13_ui_test.sh
```

ネストしたSwayを使う場合は、Swayウィンドウが開き、その中でUIとWaydroidが起動する。Raspberry Pi 5実機ではネスト方式を使わず、ログイン時にSwayセッションを選択して起動する。

ネストしたSwayへ切り替える前に、起動スクリプトが元の端末で`sudo`のパスワードを一度だけ確認する。パスワードをファイルへ保存しない。Androidホーム表示については、`scripts/install_waydroid_home_sudoers.sh`が作成するHOMEキー送信専用の`NOPASSWD`規則だけを許可し、Waydroid全体や任意コマンドを対象にしない。

VirtualBox上のネストSwayでは、DMA-BUFとGPUの組み合わせによってWaylandプロトコルエラーが発生する場合がある。自動起動スクリプトはVM用に`WLR_RENDERER=pixman`と`WLR_NO_HARDWARE_CURSORS=1`を設定し、ソフトウェア描画で起動する。実機の専用SwayセッションではこのVM用設定を強制しない。

ネストSwayの起動時には、Qtとデスクトップポータルのアプリ識別用として`~/.local/share/applications/l880k-car-navigation.desktop`を自動作成する。これは画面起動に必要なアプリ本体ではなく、ポータルの警告を抑えるための登録情報である。

ナビゲーションを選択すると、Waydroidのホーム画面を表示した後に`net.osmand.plus`を起動し、WaydroidウィンドウをUIの固定ナビ表示領域へ移動・リサイズする。Waydroidの実ウィンドウが表示状態になる間、QMLのデモ用地図・案内カードは表示しない。OsmAndが配置されない場合は、`/tmp/l880k-navigation-commands.log`と`/tmp/l880k-navigation-manager.log`を確認し、`waydroid_window_identifiers`へ実際のウィンドウ識別子を追加する。

### 12.4 今回の準備を最初から実施する手順

Debian 13仮想環境で、最新ソース取得からSway上のUI・Waydroid・OsmAndの試験起動までを行う手順を示す。以下はSwayセッションへログインした後に実行する。

```bash
cd ~/codex-latest
git pull --ff-only public main

sudo apt update
sudo apt install sway
mkdir -p ~/.config/sway
cp src/raspberry_pi5/systemd/sway/l880k-car-navigation-sway.conf ~/.config/sway/config
```

一度ログアウトし、ログイン画面で`Sway`を選択してログインする。Swayへログイン後、次の確認を行う。

```bash
echo "$XDG_CURRENT_DESKTOP"
echo "$WAYLAND_DISPLAY"
swaymsg -t get_version
```

`swaymsg -t get_version`が成功したら、WaydroidとUIの依存関係を準備する。

```bash
sudo apt install python3-dbus python3-gi gir1.2-glib-2.0
sudo bash ./scripts/install_waydroid_home_sudoers.sh "$USER"
python3 -m venv --system-site-packages .venv-waydroid
source .venv-waydroid/bin/activate
python -m pip install -r src/raspberry_pi5/requirements-ui.txt
deactivate
```

WaydroidのBinder・コンテナが利用可能であることを確認する。

```bash
ls -l /dev/binder /dev/binderfs/binder /dev/anbox-binder 2>/dev/null
sudo systemctl start waydroid-container
waydroid status
```

最後に、同じSway端末でテストスクリプトを起動する。

```bash
./scripts/start_debian13_ui_test.sh
```

スクリプトはWaydroid CLIをシステムPython、UIを`.venv-waydroid`で起動する。ナビゲーションを選択すると、`net.osmand.plus`を起動し、Swayの固定ナビ表示領域へWaydroidウィンドウを配置する。確認できない場合は、次のログを保存する。

```bash
tail -n 100 /tmp/l880k-navigation-manager.log
tail -n 100 /tmp/l880k-navigation-commands.log
```

開発段階ではOsmAndを先行対象とし、LIVIの選択・起動は保留する。テストスクリプトは、Waydroidコンテナ、Waydroidセッション、Android起動完了、Waydroidナビ管理、PySide6/QML UIの順に起動する。`Session: RUNNING`の後に`sys.boot_completed=1`を確認するため、Androidのホーム画面やSystemUIが準備中のままUIだけが先に表示される状態を避ける。UI接続後は管理プログラムがOsmAndを先行起動して非表示で待機させ、ナビゲーション選択時に固定表示領域へ配置する。

### 12.5 Wayland外部サーフェス完全埋め込みの技術案

OsmAndの画面をQMLの`Item`内部へ完全に埋め込むことは技術的には可能である。ただし、現在のPySide6/QMLアプリへ外部ウィンドウを貼り付ける機能ではなく、L880K UI自身をWaylandコンポジタとして動作させる構成が必要になる。

想定する構成は次のとおりである。

```text
L880K専用Waylandコンポジタ
├─ QML製の車載UI
└─ Waydroid / OsmAndのWaylandサーフェス
   └─ QMLのWaylandQuickItemまたはShellSurfaceItem
```

必要となる主な実装要素を以下に示す。

| 実装要素 | 内容 |
|---|---|
| Waylandコンポジタ | Qt Wayland Compositorでクライアント接続とサーフェスを管理する |
| サーフェス表示 | Waydroidのサーフェスを`WaylandQuickItem`または`ShellSurfaceItem`としてQMLへ配置する |
| 入力転送 | タッチ、キーボード、フォーカスをOsmAndへ転送する |
| GPUバッファ共有 | Waydroidの画面バッファをQt Quickで表示する |
| ライフサイクル管理 | OsmAndの起動、終了、画面破棄、再接続を処理する |
| 表示権限制御 | バックカメラ表示や警告表示との重なり順を制御する |

この方式は、Waydroidの接続先コンポジタ変更、Qt Wayland Compositorの導入、GPUバッファ共有、タッチ入力の検証が必要であり、現在のSway構成より実装規模と検証範囲が大きい。したがって、完全埋め込み方式は将来の技術検証項目とし、現行製品構成には採用しない。

### 12.6 現行採用方式: Swayによる固定領域配置

Raspberry Pi 5実機とDebian 13仮想環境では、現実的に安定して検証できるSway方式を採用する。PySide6/QML UIとWaydroidを同じSway Waylandコンポジタ上で動かし、WaydroidをQML内部へ取り込むのではなく、固定したナビ表示領域へ外部ウィンドウとして配置する。

```text
Sway Waylandコンポジタ
├─ L880K NAV（PySide6/QML）
│  └─ 固定ナビ表示領域
└─ Waydroid / OsmAnd
   └─ 固定領域へ移動・リサイズ
```

採用方式の動作を以下に定める。

1. UIはPySide6/QMLのネイティブ画面として起動する。
2. UI起動後、Waydroidナビ管理が`net.osmand.plus`を先行起動する。
3. 先行起動でWaydroidウィンドウが生成された場合は、Swayのスクラッチパッドへ退避する。
4. ナビゲーション選択時は、起動済みOsmAndの表示面を戻す。
5. `swaymsg -t get_tree -r`でWaydroidウィンドウを検出する。
6. `floating enable`、`move position`、`resize set`を使って固定ナビ表示領域へ配置する。
7. Waydroidの実画面が表示状態になるまで、QMLのデモ用地図と仮の案内情報は表示しない。
8. Waydroidウィンドウを検出できない場合は表示成功とせず、管理ログへ原因を記録する。

Debian 13のSway試験では、UIをフルスクリーンにするとWaydroidの外部ウィンドウがUIの背面に隠れるため、UIを通常の1280×720ウィンドウとしてSwayへ配置する。Waydroidを固定領域へ配置した後に前面化することで、UIのヘッダー・下部ナビゲーションとOsmAndの表示領域を同時に表示する。Raspberry Pi 5実機でもSway方式を採用する場合は同じ表示順とし、完全フルスクリーン指定は使用しない。

この方式では、OsmAndは別プロセス・別ウィンドウのままだが、同じコンポジタ上で固定領域に配置されるため、利用者からはナビ画面へ組み込まれたように見える。完全埋め込み方式へ移行する場合は、別途プロトタイプを作成し、GPU、入力、画面切替、復帰処理を実機で検証してから設計を更新する。

### 12.6.1 VirtualBoxでGPU描画を試験する

現在のDebian 13試験スクリプトは、VirtualBoxの3Dアクセラレーションが無効でも起動できるよう、Swayの描画方式を`pixman`（CPU描画）に固定している。そのためWaydroidとUIの動作が重くなる。wlrootsでは`WLR_RENDERER`に`gles2`または`pixman`を指定でき、`gles2`は`WLR_RENDERER_ALLOW_SOFTWARE=0`にするとソフトウェア描画へフォールバックせず、GPU初期化の成否を確認できる。

VirtualBoxを終了した状態で、VMの設定を次のようにする。

1. グラフィックコントローラーを`VMSVGA`にする。
2. ビデオメモリーを可能な範囲で増やす。
3. `3Dアクセラレーションを有効化`を有効にする。
4. Debian側へGuest AdditionsとMesa診断ツールを導入する。

```bash
sudo apt install virtualbox-guest-utils virtualbox-guest-x11 mesa-utils
glxinfo -B
```

`glxinfo -B`で`Accelerated: yes`を確認できた場合は、GPU描画を試験する。

```bash
cd ~/codex-latest
./scripts/stop_debian13_ui_test.sh
L880K_NAV_RENDERER=gles2 ./scripts/start_debian13_sway_ui_test.sh
```

Swayが起動しない、画面が乱れる、またはWaylandプロトコルエラーが出る場合は、Pixmanへ戻す。

```bash
./scripts/stop_debian13_ui_test.sh
./scripts/start_debian13_sway_ui_test.sh
```

VirtualBoxの3Dアクセラレーションがゲストへ十分に提供されない場合、`glxinfo -B`が`Accelerated: no`となる。この場合、`gles2`を指定してもGPU描画にはならないため、実機のRaspberry Pi 5での検証を優先する。

### 12.7 Binderデバイスが見つからない場合

UIで`実行環境を確認できません: binder_device`と表示された場合は、管理プログラムが次のいずれのBinderデバイスも確認できていない。

```text
/dev/binder
/dev/binderfs/binder
/dev/anbox-binder
```

まずカーネルモジュールとデバイスの有無を確認する。

```bash
sudo modprobe binder_linux
ls -l /dev/binder /dev/binderfs/binder /dev/anbox-binder 2>/dev/null
ls -l /sys/class/misc | grep binder
grep -E '^CONFIG_ANDROID_BINDER|^# CONFIG_ANDROID_BINDER' "/boot/config-$(uname -r)"
```

`/dev/anbox-binder`だけが存在する環境は、管理プログラムの候補判定で利用可能として扱う。3種類とも存在しない場合は、Binder IPCを有効にしたカーネル、またはBinderFSを利用できるカーネルが必要である。`/dev/binder`がないことだけを理由に、存在しないデバイスを`mknod`で作成してはならない。カーネルがBinderデバイスを提供していることを確認してからWaydroidを再起動する。
