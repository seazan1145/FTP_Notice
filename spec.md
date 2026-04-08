# FTP監視通知ツール 仕様メモ

詳細仕様は依頼文に準拠。

- 監視: FTP/FTPS を 60 秒ごとにポーリング
- `remote_dirs` は 1 接続で複数ディレクトリを監視可能
  - 優先: `|` 区切り
  - 互換: `|` が無い場合は CSV（`,`）を許容
  - 各要素は trim / 空要素除外
  - 有効な `remote_dirs` が 0 件なら設定エラー
- 判定: 新規かつ 30 秒以上サイズ変化なしで通知
- 永続化: SQLite (`data/monitor.db`)
- 通知: Windows トースト通知
- ログ: `logs/monitor_YYYY-MM-DD.log`
