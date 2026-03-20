# FTP Monitor Notifier

## 概要
Windows 11 向けの FTP/FTPS 監視通知ツールです。INI で複数接続先・複数監視フォルダを設定し、ポーリングで新規ファイル候補を検知します。ファイルサイズが最後に変化してから一定時間（`stable_seconds`）経過したときだけ通知し、SQLite に状態保存して二重通知を防ぎます。

通知手段は `windows` / `mail` / `both` を切り替え可能です。

## セットアップ
1. Python 3.10+ をインストール
2. プロジェクト直下で依存関係をインストール

```bash
pip install -r requirements.txt
```

Windows 通知を有効化するには追加で以下をインストールしてください（未導入時は通知は表示されません）。

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
- `notification_mode`: `windows` / `mail` / `both`

起動時に不正設定があれば、項目名つきでエラー終了します。

### 通知モード
- `windows`: 従来の Windows トースト通知のみ
- `mail`: メール送信のみ（成功時のみ `is_notified=1`）
- `both`: Windows とメールの両方実行し、**両方成功時のみ** `is_notified=1`

### メール設定
`[general]` に以下を設定します。

```ini
notification_mode = mail
mail_module_path = mail.py
mail_enabled = true
mail_smtp_server = smtp.gmail.com
mail_smtp_port = 587
mail_from_address = your_sender@gmail.com
mail_to_address = your_receiver@example.com
mail_subject = [FTPWATCH] updated
mail_use_tls = true
mail_username = your_sender@gmail.com
mail_password =
```

メールパスワードはコードに直書きせず、環境変数 `FTP_NOTICE_MAIL_PASSWORD` を推奨します。

```bash
set FTP_NOTICE_MAIL_PASSWORD=your_app_password
```

> Gmail 利用時は通常パスワードではなく、アプリパスワードの利用を推奨します。

## JSON メール本文
mail モードでは、通知本文は JSON 文字列（`text/plain; charset=utf-8`）で送信されます。
Outlook + Power Automate + SharePoint + Teams 連携では、本文 JSON をそのまま解析する前提です。

主なキー:
- `path`
- `fileName`
- `folder`
- `lastModified`
- `size`
- `status` (`updated`)
- `lastChecked`
- `hashKey` (`remote_path + file_size`)

## 起動方法
通常起動:

```bash
python -m app.main
```

1回巡回だけ実行:

```bash
python -m app.main --once
```

通知テスト（成功時 0 / 失敗時 1 を返す）:

```bash
python -m app.main --test-notify
```

## ログとDBの保存場所
- ログ: `logs/monitor_YYYY-MM-DD.log`
- DB: `data/monitor.db`

## セキュリティ注意（重要）
INI はパスワードを平文保存します。以下を徹底してください。
- 実運用認証情報は配布用ファイルに含めない
- 実運用 INI は Git / zip / 共有フォルダに含めない
- 共有前に設定ファイル内の認証情報を必ずマスク

推奨:
- `mail_password` は未設定にし、`FTP_NOTICE_MAIL_PASSWORD` を利用する

将来的な改善候補:
- Windows Credential Manager
- DPAPI 暗号化
- 認証情報の別ファイル分離
