# FTP Monitor Notifier

## 概要
Windows 11 向けの FTP/FTPS 監視通知ツールです。INI で複数接続先・複数監視フォルダを設定し、ポーリングで新規ファイル候補を検知します。ファイルサイズが最後に変化してから一定時間（`stable_seconds`）経過したときだけ通知し、SQLite に状態保存して二重通知を防ぎます。

## セットアップ
1. Python 3.10+ をインストール
2. プロジェクト直下で依存関係をインストール

```bash
pip install -r requirements.txt
```

通知を有効化するには追加で以下をインストールしてください（未導入時は通知は表示されません）。

```bash
pip install win10toast
```

## 設定ファイル
- 同梱サンプル: `config/ftp_monitor.sample.ini`
- 実運用設定: `config/ftp_monitor.ini`（起動時はこちらを優先して読み込みます）
- 初回起動で `config/ftp_monitor.ini` が存在しない場合、sample から自動生成して終了します（その場では監視しません）。
- 生成後に `ftp.example.com` などの sample 値を実サーバー値へ置き換えてから再実行してください。

```bash
python -m app.main
```

### 主要パラメータ
- `protocol`: `ftp`, `ftps-explicit`, `ftps-implicit`
  - 互換入力: `ftps`, `ftpsi`, `implicit-ftps`（内部で正規化）
- `remote_dirs`: 必須（空はエラー）
- `poll_seconds`, `stable_seconds`, `connect_timeout`, `read_timeout`: 正の整数必須

起動時に不正設定があれば、項目名つきでエラー終了します。

## 起動方法
通常起動:

```bash
python -m app.main
```

1回巡回だけ実行:

```bash
python -m app.main --once
```

FTPS の詳細デバッグ（接続モード、PASV、prot_p、MLSD/LIST 開始終了）:

```bash
python -m app.main --once --debug
```

通知テスト（成功時 0 / 失敗時 1 を返す）:

```bash
python -m app.main --test-notify
```

バッチ起動:

```bat
run_monitor.bat
```


### サンプル値検出について
以下のようなダミー値が残っている接続は、起動時に警告を出したうえで無効化されます。

- `ftp.example.com`（ダミー値）
- `example.com`
- `your_host`
- `your_user` / `your_username`
- `your_password` / `CHANGEME`

`ftp.example.com` はサンプル値のため、そのままでは名前解決エラーになります。

## FTPS/FTP 一覧取得について
- まず `MLSD` を試行
- 失敗した場合は `LIST` へフォールバック
- FTPS データ接続の TLS/session 問題（`425 ... TLS session ... not resumed` など）は専用ログで識別
- `MLSD` と `LIST` が連続で失敗した場合、LIST パース問題ではなく FTPS データ接続問題の可能性を明示

## ログとDBの保存場所
- ログ: `logs/monitor_YYYY-MM-DD.log`
- DB: `data/monitor.db`

## セキュリティ注意（重要）
INI はパスワードを平文保存します。以下を徹底してください。
- 実運用認証情報は配布用ファイルに含めない
- 実運用 INI は Git / zip / 共有フォルダに含めない
- 共有前に設定ファイル内の認証情報を必ずマスク

将来的な改善候補:
- Windows Credential Manager
- DPAPI 暗号化
- 認証情報の別ファイル分離
