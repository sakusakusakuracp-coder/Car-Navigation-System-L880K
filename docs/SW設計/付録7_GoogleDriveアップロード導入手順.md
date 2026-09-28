# 付録7 Google Driveアップロード導入手順

| 項目 | 内容 |
| --- | --- |
| 対象 | Raspberry Pi 5 / Debian系Linux、Python 3.11以上 |
| 対応設計 | [07 Google Driveアップロード・削除](詳細設計/07_GoogleDriveアップロード.md) |
| 実装 | `src/raspberry_pi5/drive_upload_service.py`、`drive_upload/` |
| 更新日 | 2026-09-25 |
| 検証範囲 | 疑似Driveによる中断・再開・照合・UI状態試験。実GoogleアカウントとLinux実機の削除試験は未実施 |

## 1. 運用条件

録画が完了した動画だけをSQLite台帳へ登録し、承認したテザリング接続からGoogle Driveへ順番に送信する。通信切断後はサーバー側の受領済み位置を確認して再開する。Googleアカウント、保存フォルダ、ファイルID、サイズ、MD5を照合するまで完了扱いにしない。

初期設定では、アップロードとローカル自動削除はともに無効。実機で保存内容を確認してから順番に有効化する。既存フォルダ全体を走査・同期・削除するソフトではない。

録画サービスは確定済みMKVを`CatalogReader.register_ready()`で自動登録する。[付録8 カメラ表示・録画導入手順](付録8_カメラ表示録画導入手順.md)に従って同じ録画先・台帳パスを設定する。手動登録も利用できる。自動録画から実Google Drive保存までの連続運用は実機統合試験が必要。

## 2. 依存ソフトと保存領域

以下はリポジトリを`~/codex-latest`へ配置した例。UIで使用している仮想環境がある場合は、そのPythonで依存ライブラリを導入してもよい。

```bash
cd ~/codex-latest
sudo apt update
sudo apt install python3-venv ffmpeg iproute2 network-manager
python3 -m venv .venv
.venv/bin/python -m pip install -r src/raspberry_pi5/requirements-drive.txt
install -d -m 700 ~/l880k-recordings
install -d -m 700 ~/.local/state/l880k
install -d -m 700 ~/.config/l880k-car-navigation
cp -n src/raspberry_pi5/config/drive_upload.example.json ~/.config/l880k-car-navigation/drive_upload.json
chmod 600 ~/.config/l880k-car-navigation/drive_upload.json
```

`recording_root`は実際のSSD上の録画専用ディレクトリへ変更する。録画以外のデータを混在させない。台帳と認証ファイルは録画領域の外へ置く。ネットワークの管理方式を切り替える作業は遠隔接続が切れる可能性があるため、この手順では行わない。NetworkManagerで接続が管理されていない場合、本サービスは送信待機となる。

## 3. Googleアカウントの初回認証

1. [Google Cloud Console](https://console.cloud.google.com/)で使用するプロジェクトを用意し、Google Drive APIを有効にする。
2. OAuth同意画面を設定し、必要に応じて使用アカウントをテストユーザーへ追加する。
3. OAuthクライアントを「デスクトップアプリ」として作成する。ダウンロードしたJSONを`~/.config/l880k-car-navigation/client_secret_drive.json`へ配置する。公開リポジトリへ保存しない。
4. 次のコマンドを**デスクトップ利用者の権限**で実行し、ブラウザで同意する。`sudo`で実行しない。

```bash
cd ~/codex-latest
chmod 600 ~/.config/l880k-car-navigation/client_secret_drive.json
.venv/bin/python src/raspberry_pi5/drive_upload_service.py \
  --config ~/.config/l880k-car-navigation/drive_upload.json \
  authorize --client-secrets ~/.config/l880k-car-navigation/client_secret_drive.json
```

要求する権限は`drive.file`。このアプリで作成・許可されたファイルに範囲を限定する。認証トークンは設定の`credential_path`へ保存される。トークン、OAuthクライアントJSON、台帳に含まれる再開URIをGit・スクリーンショット・ログ共有へ含めない。`.gitignore`は代表的なファイル名を除外するが、別名で保存した秘密ファイルまで保護するものではない。

### 3.1 SSH接続で認証する場合

手元PCからポート転送付きで接続する。`pi-host`はPiのホスト名またはIPへ変更する。

```bash
ssh -L 8765:localhost:8765 admin@pi-host
```

そのSSH端末上で、前記認証コマンドの末尾へ`--no-browser --port 8765`を追加する。表示された認証URLを手元PCのブラウザで開く。認証後のlocalhostへの転送がSSH経由でPiへ届く。認証は5分以内に完了する。ポートをインターネットへ公開する必要はない。

### 3.2 保存先フォルダの作成

```bash
cd ~/codex-latest
.venv/bin/python src/raspberry_pi5/drive_upload_service.py \
  --config ~/.config/l880k-car-navigation/drive_upload.json \
  create-folder --name "L880K Recordings"
```

表示された`account_ref`と`folder_id`を設定JSONの同名項目へ入力する。任意の既存フォルダIDを指定しても、`drive.file`の権限ではアクセスできない場合があるため、初回はこのコマンドで作成する。`authorize`と`create-folder`は利用者が明示実行する初期設定処理であり、テザリング制限や日次送信量制限の対象外。

## 4. テザリングと送信量の設定

```bash
nmcli -f NAME,UUID,TYPE,DEVICE connection show --active
```

スマートフォンのテザリング接続に対応するUUIDだけを、`allowed_profile_ids`へ追加する。SSIDや接続名ではなくUUIDを使用する。

| 設定 | 初期値・意味 |
| --- | --- |
| `enabled` | `false`。認証・送信先・接続UUIDの設定後に`true`へ変更 |
| `auto_delete_enabled` | `false`。検証後もローカル動画を保持 |
| `exclusive_recording_root` | `false`。録画領域の専用化と共通ロックの運用を確認した場合のみ`true` |
| `account_ref` / `folder_id` | 初期設定コマンドで取得した保存先情報 |
| `allowed_profile_ids` | 承認した接続UUIDの配列。例: `["12345678-1234-1234-1234-123456789abc"]` |
| `chunk_bytes` | `1048576`。1MiB単位、256KiBの倍数。最大8MiB |
| `daily_byte_budget` | `1073741824`。UTC日付単位で1GiB。再送・結果不明の送信も使用量に含める |
| `bandwidth_bytes_per_second` | `1048576`。平均1MiB/秒を上限とする送信間隔制御 |
| `io_timeout_s` | `15`。HTTPの接続・読取タイムアウト。総処理時間やDNS解決時間の上限ではない |
| `poll_interval_s` | `2`。待機中の処理確認間隔 |
| `max_retries` | `8`。一時障害の再試行回数の上限 |

設定変更はサービスを停止してから行い、再起動する。すでに処理を始めた台帳のGoogleアカウント・フォルダを変更した場合は処理を停止する。別の保存先へ移行するときは、既存台帳を編集せず、残件を確認したうえで別台帳を使用する。

## 5. 動画1件での確認

個人情報を含まない短い試験動画を作成する。以下のファイル名が未使用であることを確認する。`ffmpeg -n`は既存ファイルを上書きしない。

```bash
ffmpeg -n -f lavfi -i testsrc=size=320x240:rate=10 -t 3 \
  -c:v mpeg4 -pix_fmt yuv420p ~/l880k-recordings/upload-test.mp4
cd ~/codex-latest
.venv/bin/python src/raspberry_pi5/drive_upload_service.py \
  --config ~/.config/l880k-car-navigation/drive_upload.json \
  register --closed-file upload-test.mp4 --camera test
.venv/bin/python src/raspberry_pi5/drive_upload_service.py \
  --config ~/.config/l880k-car-navigation/drive_upload.json run --dry-run
```

手動登録はffprobeによる動画形式確認とファイルのMD5計算を行う。録画中のファイルは登録しない。`--dry-run`ではネットワーク送信・削除・録画行の状態変更を行わない。

Googleアカウント・保存先・接続UUIDを設定し、`enabled`だけを`true`にしてから次を実行する。

```bash
.venv/bin/python src/raspberry_pi5/drive_upload_service.py \
  --config ~/.config/l880k-car-navigation/drive_upload.json run --once
.venv/bin/python src/raspberry_pi5/drive_upload_service.py \
  --config ~/.config/l880k-car-navigation/drive_upload.json status
```

`--once`は対象1件を処理して終了する。通信不可なら待機状態で終了するため、終了コード0だけでアップロード成功と判断しない。台帳の`counts.VERIFIED`とDrive側の動画を確認する。動画名は`file_id-元のファイル名`となる。ブラウザからダウンロードして再生と内容を確認し、元動画がローカルに残っていることも確認する。

## 6. 常駐とUI表示

UI設定画面から送信の一時停止・再開が可能。専用ソケットの`pause` / `resume`で台帳へ保存する。認証、送信許可、ネットワーク条件、自動削除はこの操作で変更しない。適用が確認できない場合は再照会し、成功したものとして扱わない。[付録9](付録9_UI追加機能導入手順.md)参照。

ターミナルで動作確認する場合:

```bash
cd ~/codex-latest
.venv/bin/python src/raspberry_pi5/drive_upload_service.py \
  --config ~/.config/l880k-car-navigation/drive_upload.json run
```

Ctrl+Cで新規作業を停止し、実行中の通信終了後に終了する。台帳と受領済み位置を使って次回再開する。全動画の送信完了までは待たない。

UIと同じLinuxユーザーで起動すると、カメラ画面の「クラウド同期」に状態と未完了件数を表示する。既定ソケットは`$XDG_RUNTIME_DIR/l880k-drive-upload.sock`。実行環境により異なる場合は、サービスの`--socket`とUIの`L880K_DRIVE_SOCKET`を同じ絶対パスへ設定する。UIは1秒間隔で照会し、通知の有効期限を超えた場合や接続切れ時は正常表示を維持しない。

**UI設定画面のクラウド関連スイッチは、まだこのサービスの設定変更へ接続していない。** 送信条件は設定JSON、休止・再開は下記CLIで操作する。既存のDebian UI起動スクリプトもこのサービスを自動起動しない。開発中は別ターミナル、常用時は次のuser unitを使用する。

### 6.1 systemdの利用

同梱unitはリポジトリが`~/l880k-car-navigation`にある前提。別の配置なら、コピー後に`WorkingDirectory`、`ExecStart`、`PYTHONPATH`を実際の場所へ変更する。

```bash
install -d ~/.config/systemd/user
cp src/raspberry_pi5/systemd/l880k-drive-upload.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now l880k-drive-upload.service
systemctl --user status l880k-drive-upload.service
journalctl --user -u l880k-drive-upload.service -n 80 --no-pager
```

停止は`systemctl --user stop l880k-drive-upload.service`。user unitのため、OS起動直後からログイン前にも動かす運用は別途ユーザーの常駐設定が必要。unitの強制停止期限は90秒。長時間のDNS待ちなどで強制停止された場合も、次回サーバーの受領位置を照会する。

## 7. 休止・再開・再試行

```bash
.venv/bin/python src/raspberry_pi5/drive_upload_service.py --config ~/.config/l880k-car-navigation/drive_upload.json pause
.venv/bin/python src/raspberry_pi5/drive_upload_service.py --config ~/.config/l880k-car-navigation/drive_upload.json resume
```

送信中の場合は次の制御確認位置から休止する。`resume`は利用者の休止を解除するだけであり、`enabled=false`や未許可ネットワークを回避しない。

`status`の`problems`に理由と`file_id`を表示する。認証切れ・Drive空き容量不足などを解消した後は、サービスを停止して`retry --file-id 対象ID`を実行し、再起動する。内容不一致・元ファイル置換・削除証拠不良はこの操作では解除しない。台帳を直接書き換えて強制完了にしない。

| 主な理由 | 対応 |
| --- | --- |
| `NETWORK_NOT_ALLOWED` | テザリングのUUIDと実際の経路を確認 |
| `NETWORK_INSPECTION_UNAVAILABLE` / `NETWORK_ROUTE_UNKNOWN` | NetworkManager、`ip`、DNS、経路情報を確認 |
| `NETWORK_OFFLINE` | 到達できる通信経路がないため接続を待つ |
| `DAILY_BUDGET_EXHAUSTED` | 日次上限。翌UTC日付まで待機または運用上限を見直す |
| `AUTHENTICATION_REQUIRED` | 初回認証をやり直し、対象のBLOCKEDを明示再試行 |
| `DRIVE_STORAGE_FULL` | Google Drive容量を確認。ローカル動画は保持される |
| `LOCAL_IO_ERROR` | SSD・権限・パスを確認。自動で再試行解除しない |

## 8. 録画側との接続と自動削除

録画側は書込みを終了し、close・fsync・再生可能性の確認後に、同じ台帳へ`register_ready(relative_path, camera_id, file_id)`を呼ぶ。登録後の動画を追記・置換・移動しない。登録済みパスは再利用せず、セグメントごとに一意な名前にする。

台帳の`file_guard()`は登録と削除が共用するファイルロック。他方が使用中なら`Paused("RECORDING_CATALOG_BUSY")`を返す。アップロード側は待機し、録画側も登録を再試行して未登録ファイルを残さない。録画側が登録済み動画を操作する必要がある場合も同じロックを使用する。録画ルートと配下ディレクトリはサービス所有者だけが書込み可能とし、シンボリックリンク・ハードリンク・共有書込み領域は使用しない。

自動削除を有効にする前に、Linux実機で次を確認する。

1. アップロードした動画の取得・再生・内容照合。
2. 通信途中の切断、Google認証切れ、容量不足で元動画が残ること。
3. 停止・再起動後に重複作成せず再開すること。
4. 元動画やDriveファイルを変更した場合、削除せずBLOCKEDになること。
5. 実際の録画サービスと共通台帳・ロックが機能すること。

確認後に限り`exclusive_recording_root=true`と`auto_delete_enabled=true`を設定する。削除はLinux限定で、直前にもDriveとローカルの内容を再検査する。`DELETING`と削除予定を永続化してからunlinkし、完了後に`DELETED`を記録する。削除後にGoogle側の動画を消した場合、復元はできない。

## 9. 残る懸念点と試験

| 項目 | 現状・追加確認 |
| --- | --- |
| 実Drive | 通常送信、トークン更新、権限不足、容量不足を実アカウントで確認する |
| Linux削除 | WindowsではLinux専用の削除・リンク検査テストをスキップ。実機で必ず実施 |
| 通信経路 | 各HTTP要求前に到達可能なDNS候補の経路を確認。IPv6経路がないだけならIPv4を使用可能。検査後の経路変更までは固定しない |
| 通信費 | 日次予算は動画ペイロードの予約量。DNS・TLS・メタデータ・認証通信の全バイトを計測するものではない |
| 性能・熱 | 固定帯域制御と低優先度を使用。録画・描画との競合、SSD書込み負荷、CPU温度による可変制限は実機評価が必要 |
| SQLite | 破損時は停止。台帳のバックアップ・ディスク満杯時の復旧手順は運用確認が必要 |
| UI連携 | 状態表示のみ。設定変更や手動再試行ボタンとの連携は未実装 |

自動テストはGoogleへ通信しない。疑似動画と疑似Driveで障害を注入する。

```bash
cd ~/codex-latest
PYTHONPATH=src/raspberry_pi5 .venv/bin/python -m unittest discover -s tests/drive_upload -v
```

参考: [Drive再開可能アップロード](https://developers.google.com/workspace/drive/api/guides/manage-uploads)、[Driveファイルの属性](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)、[デスクトップアプリのOAuth](https://developers.google.com/identity/protocols/oauth2/native-app)。
