# QML画面素材

テスラライクなダークテーマで使用する画面素材です。色や角丸は`Theme.qml`に集約し、画面部品は`components/`から再利用します。

## 素材一覧

- `assets/home_background.png`: ホーム画面の背景。写真の上に半透明の暗幕を重ねて文字の視認性を確保します。
- `assets/icons/navigation.svg`: ナビゲーション
- `assets/icons/camera.svg`: カメラ
- `assets/icons/music.svg`: 音楽
- `assets/icons/vehicle.svg`: 車両情報
- `assets/icons/warning.svg`: 警告表示
- `assets/icons/recording.svg`: 録画状態
- `assets/icons/locate.svg`: 現在地・再検索
- `assets/icons/plus.svg`, `assets/icons/minus.svg`: 地図拡大縮小

## QML部品

- `components/TeslaButton.qml`: ダークテーマの操作ボタン
- `components/IconTile.qml`: ホーム画面の機能タイル
- `components/StatusChip.qml`: 車両状態・サービス状態の表示
- `components/WarningBadge.qml`: 警告表示

SVGはQMLの`Image`から直接読み込み、PNGは`Image`の背景として読み込みます。部品の利用時は画面側で`import "components"`を指定します。
