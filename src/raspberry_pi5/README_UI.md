# カーナビUI 実装

このフォルダは、[カーナビUI詳細設計書](../../docs/SW設計/詳細設計/01_カーナビUI.md)に基づくPython + PySide6/QMLの実装です。

## 起動

```powershell
cd C:\Users\android\Documents\Codex\2026-05-26\codex
python -m venv .venv
.venv\Scripts\python -m pip install -r src\raspberry_pi5\requirements-ui.txt
$env:PYTHONPATH = "src\raspberry_pi5"
.venv\Scripts\python src\raspberry_pi5\main_app.py
```

現在の `ServiceBridge` は、画面の動作確認用スタブです。Waydroid、カメラ、OBD2、Mopidyなどの実サービスへ接続する処理は、各詳細設計書の通信契約に合わせてアダプタとして追加します。

Python側では、関数のdocstringに「なぜその処理を行うか」を日本語で記載しています。QMLは表示とタッチ操作の通知だけを担当し、GPIO、ファイル保存、外部サービス通信を直接行いません。
