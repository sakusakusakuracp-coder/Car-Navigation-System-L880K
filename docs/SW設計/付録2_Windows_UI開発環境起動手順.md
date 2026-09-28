# 付録2 WindowsでのカーナビUI開発環境起動手順

[ソフトウェア設計図へ戻る](ソフトウェア設計図.md)

## 目次

- [1. 目的・適用範囲](#app2-1)
- [2. Pythonの確認](#app2-2)
- [3. PySide6のインストール](#app2-3)
- [4. UIの起動](#app2-4)
- [5. Pythonの実体が複数ある場合](#app2-5)
- [6. 起動できない場合の確認](#app2-6)

<a id="app2-1"></a>

## 1. 目的・適用範囲

Windows上で、Raspberry Pi 5向けに作成したPython + PySide6/QMLのカーナビUIを、開発・画面確認用に起動する手順を示す。

Windowsでの起動は画面や画面切替の確認を目的とする。GPIO、Waydroid、カメラ、OBD2、Mopidyなどの車載機能がWindows上で動作することを意味しない。外部サービス連携は、現在は動作確認用のスタブである。

<a id="app2-2"></a>

## 2. Pythonの確認

PowerShellを開き、プロジェクトフォルダへ移動する。

```powershell
cd C:\Users\android\Documents\Codex\2026-05-26\codex
python --version
python -c "import sys; print(sys.executable)"
```

`python --version`でバージョンが表示され、`sys.executable`がPython本体のパスを示せば使用できる。

次のように`C:\Users\android\AppData\Local\Microsoft\WindowsApps\python.exe`だけが表示され、バージョンが表示されない場合は、Python実行エイリアスだけが有効になっている。Python公式インストーラーを使用し、インストール時に`Add python.exe to PATH`を有効にする。

インストール後はPowerShellを開き直して、もう一度バージョンを確認する。

<a id="app2-3"></a>

## 3. PySide6のインストール

`pip`単体ではなく、起動に使用するPythonから`pip`を呼び出す。

```powershell
python -m pip install -r src\raspberry_pi5\requirements-ui.txt
```

PySide6の導入状態は次で確認する。

```powershell
python -c "import PySide6; print(PySide6.__version__)"
```

`No module named pip`と表示された場合は、Pythonへpipを追加してから再実行する。

```powershell
python -m ensurepip --upgrade
python -m pip install --upgrade pip
python -m pip install -r src\raspberry_pi5\requirements-ui.txt
```

<a id="app2-4"></a>

## 4. UIの起動

プロジェクトフォルダで、Pythonモジュールを解決するための`PYTHONPATH`を設定する。

```powershell
cd C:\Users\android\Documents\Codex\2026-05-26\codex
$env:PYTHONPATH = "src\raspberry_pi5"
python src\raspberry_pi5\main_app.py
```

起動すると、ホーム画面が表示される。下部の操作ボタンからホーム、ナビ、カメラ、音楽、車両情報、設定の画面を切り替えられる。

停止する場合は、UIのウィンドウを閉じるか、起動したPowerShellで`Ctrl+C`を入力する。

<a id="app2-5"></a>

## 5. Pythonの実体が複数ある場合

`pip install`ではPySide6がインストール済みなのに、起動時に`ModuleNotFoundError: No module named 'PySide6'`が出る場合は、インストールしたPythonと起動したPythonが異なる可能性がある。

次のコマンドで両方の実体を確認する。

```powershell
python -c "import sys; print(sys.executable); print(sys.version)"
python -m pip --version
```

表示されたPythonの場所が異なる場合は、PySide6が入っているPythonの実行ファイルを直接指定する。

```powershell
$env:PYTHONPATH = "src\raspberry_pi5"
& "C:\Users\android\AppData\Local\Python\pythoncore-3.14-64\python.exe" src\raspberry_pi5\main_app.py
```

上記のパスは環境例であり、実際には`python -m pip --version`の表示に合わせる。別のPythonを使用する場合は、そのPythonに対して次を実行してから起動する。

```powershell
& "Python本体の絶対パス" -m pip install -r src\raspberry_pi5\requirements-ui.txt
```

<a id="app2-6"></a>

## 6. 起動できない場合の確認

| 症状 | 確認・対処 |
| --- | --- |
| `python`が認識されない | Pythonをインストールし、`Add python.exe to PATH`を有効にしてPowerShellを開き直す |
| `pip`が認識されない | `python -m pip`を使用する。pipがなければ`python -m ensurepip --upgrade`を実行する |
| `No module named PySide6` | 起動に使用するPythonへPySide6をインストールする |
| `can't open file` | プロジェクトフォルダへ移動してから起動する |
| QMLが見つからない | `PYTHONPATH`を`src\raspberry_pi5`に設定し、リポジトリのルートから起動する |
| 画面は起動するが外部機能が動かない | Linux専用サービスが未接続のため、未接続・不明を表示する。設定保存やMopidy HTTP接続はWindowsでも動作確認可能。車載機能はRaspberry Pi 5上で接続する |

この手順はWindowsでの開発確認用であり、Raspberry Pi 5の実機導入手順ではない。実機ではRaspberry Pi OS 64-bitの環境へ依存ライブラリを導入し、Wayland表示環境で起動する。

設定保存、音楽、録画一覧などの追加機能は[付録9 UI追加機能導入手順](付録9_UI追加機能導入手順.md)を参照する。
