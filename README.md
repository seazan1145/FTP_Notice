# FTP Monitor Notifier

## 概要
Windows 11 向けの FTP/FTPS 監視通知ツールです。INI で複数接続先・複数監視フォルダを設定し、ポーリングで新規ファイル候補を検知します。ファイルサイズが最後に変化してから一定時間（`stable_seconds`）経過したときだけ通知し、SQLite に状態保存して二重通知を防ぎます。

## セットアップ
1. Python 3.10+ をインストール
2. プロジェクト直下で依存関係をインストール

```bash
pip install -r requirements.txt
```

## 設定ファイル
- 同梱サンプル: `config/ftp_monitor.sample.ini`
- 互換のため `config/ftp_monitor.ini` もサンプル内容です
- 実運用は `config/ftp_monitor.local.ini` を作成し、`--config` で指定してください（`.gitignore` 対象）

```bash
python -m app.main --config config/ftp_monitor.local.ini
```

### 主要パラメータ
- `protocol`: `ftp`, `ftps`, `ftps-implicit`, `ftpsi`, `implicit-ftps` のみ許可
- `remote_dirs`: 必須（空はエラー）
- `poll_seconds`, `stable_seconds`, `connect_timeout`, `read_timeout`: 正の整数必須

起動時に不正設定があれば、項目名つきでエラー終了します。

## 起動方法
通常起動:

```bash
python -m app.main --config config/ftp_monitor.local.ini
```

1回巡回だけ実行:

```bash
python -m app.main --config config/ftp_monitor.local.ini --once
```

通知テスト（成功時 0 / 失敗時 1 を返す）:

```bash
python -m app.main --config config/ftp_monitor.local.ini --test-notify
```

バッチ起動:

```bat
run_monitor.bat
```

## ログ改善ポイント
- MLSD 失敗時、LIST へのフォールバックを INFO/WARNING で明示
- ディレクトリ単位の検出件数を出力
- 1巡回ごとに `detected / new_candidates / notified` のサマリを出力
- フィルタ除外理由や未通知スキップ理由を DEBUG で出力
- 通知成功/失敗を明確に分離して出力

## FTPS/FTP 一覧取得について
- まず `MLSD` を試行
- `ssl.SSLEOFError` などで失敗した場合は必ず `LIST` にフォールバック
- `LIST` は Unix 形式を優先、IIS/Windows 形式も対応
- パース不能行は warning ログを出してスキップ

## ログとDBの保存場所
- ログ: `logs/monitor_YYYY-MM-DD.log`
- DB: `data/monitor.db`

## セキュリティ注意（重要）
INI はパスワードを平文保存します。以下を徹底してください。
- 実運用認証情報は `config/ftp_monitor.local.ini` にのみ記載
- 実運用 INI は Git 管理対象にしない
- 共有前に設定ファイル内の認証情報を必ずマスク

将来的な改善候補:
- Windows Credential Manager
- DPAPI 暗号化
- 認証情報の別ファイル分離
