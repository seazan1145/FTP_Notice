# FTP Monitor Notifier

## 概要
Windows 11 向けの FTP/FTPS 監視通知ツールです。INIで複数接続先・複数監視フォルダを設定し、60秒ごとのポーリングで新規ファイル候補を検知します。ファイルサイズが一定時間（既定30秒）安定してから Windows 通知を行い、SQLite に状態保存して二重通知を防ぎます。

## セットアップ
1. Python 3.10+ をインストール
2. プロジェクト直下で依存関係をインストール

```bash
pip install -r requirements.txt
```

## INI設定
設定ファイル: `config/ftp_monitor.ini`

- `[general]` で監視間隔、安定秒数、タイムアウト、ログレベルを設定
- `[ftp_xx]` を増やして複数接続先を登録
- `remote_dirs` に監視フォルダをカンマ区切りで指定
- `include_extensions` が空なら全拡張子対象

サンプルは `config/ftp_monitor.ini` を参照してください。

## 起動方法
通常起動:

```bash
python -m app.main
```

1回巡回だけ実行:

```bash
python -m app.main --once
```

通知テスト:

```bash
python -m app.main --test-notify
```

バッチ起動:

```bat
run_monitor.bat
```

## タスクスケジューラ登録方法
1. タスク スケジューラを開く
2. 「タスクの作成」
3. トリガー: 「ログオン時」
4. 操作: `run_monitor.bat` を指定
5. 「ユーザーがログオンしているときのみ実行」を推奨

## ログとDBの保存場所
- ログ: `logs/monitor_YYYY-MM-DD.log`
- DB: `data/monitor.db`

## よくあるエラー
- 認証失敗: `host / port / username / password` を再確認
- 文字化け: `encoding` を `cp932` などに変更
- FTPS失敗: `protocol=ftps`, `port=990` とサーバー設定の整合性を確認

## FTP / FTPS の違い
- `ftp`: 平文通信
- `ftps`: TLS で暗号化通信（`ftplib.FTP_TLS` を利用）

## セキュリティ注意（パスワード平文保存）
INI は平文でパスワードを保持します。アクセス権を厳格に管理してください。将来的な改善候補:
- Windows Credential Manager
- DPAPI 暗号化
- 認証情報の別ファイル分離
