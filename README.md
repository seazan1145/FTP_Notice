# FTP Monitor Notifier

## 概要
FTP/FTPS 監視ツールです。`stable_seconds` 経過後に通知し、SQLite で重複通知を抑止します。通知モードは `windows` / `mail` / `both`。

## 設定ファイル運用（重要）
- 公開テンプレート: `config/ftp_monitor.sample.ini`
- 実運用: `config/ftp_monitor.ini`（**Git管理しない**）
- `ftp_monitor.ini` には Gmail アプリパスワードを保存するため、端末アクセス制御を必ず実施してください。

## INI 構成
```ini
[general]
poll_seconds = 60
stable_seconds = 30
mail_module_path = mail.py

[notification]
mode = mail   ; windows / mail / both

[mail]
enabled = true
provider = gmail
smtp_server = smtp.gmail.com
smtp_port = 587
use_tls = true
from_address = your_sender@gmail.com
to_address = your_receiver@example.com
subject = [FTPWATCH] updated
username = your_sender@gmail.com
password = your_app_password

[startup]
notify_existing_on_start = false
```

## Gmail 送信設定手順
1. Google アカウントで 2 段階認証を有効化。
2. Google のアプリパスワードを作成。
3. `config/ftp_monitor.ini` の `[mail]` に `username` / `password` / `from_address` / `to_address` を記載。
4. `notification.mode = mail` または `both` を設定。

## 起動時既存ファイルの扱い
- `startup.notify_existing_on_start = false`: 起動前から存在するファイルは取り込みのみ（通知なし）
- `startup.notify_existing_on_start = true`: 起動後の安定化判定で通知対象にできる

## 通知仕様
- 同一 `remote_path` でも `size` または `modified_at` が変化したら更新として再アーム。
- `is_notified=1` でも更新検知時は再通知候補に戻る。
- `mark_notified()` は有効通知が成功した場合のみ実行。
- 失敗時は未通知のまま残り、次回で再送対象。

## メール本文
- MIME: `text/plain; charset=utf-8`
- 本文: JSON のみ（Power Automate Parse JSON 前提）

```json
{
  "path": "/upload/file.png",
  "fileName": "file.png",
  "folder": "/upload",
  "lastModified": "2026-03-20T10:00:00+00:00",
  "size": 12345,
  "status": "updated",
  "lastChecked": "2026-03-20T10:00:30+00:00",
  "hashKey": "/upload/file.png_12345_2026-03-20T10:00:00+00:00"
}
```

## ログの見方
- 初期化:
  - `Notification mode resolved: ...`
  - `Windows notifier enabled: ...`
  - `Mail notifier enabled: ...`
  - `Mail transport: provider=... smtp=... tls=...`
  - `Mail routing: from=... to=...`
- ファイル判定:
  - `Candidate inserted`
  - `Skip by filter`
  - `Existing file changed`
  - `Re-armed candidate due to change`
  - `Candidate waiting stable`
  - `Candidate stable, sending notification`
  - `Marked notified` / `mark_notified skipped`

## よくある失敗例
- `notification.mode = windows` のまま
- `mail.enabled = false`
- `username` / `password` 未設定
- `from_address` / `to_address` 未設定
- Gmail アプリパスワード未設定
- `stable_seconds` 未到達
- 既存ファイル更新を検知する前にフィルタ除外

## テスト
```bash
python -m unittest discover -s tests
```
