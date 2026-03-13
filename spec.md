# FTP監視通知ツール 仕様メモ

詳細仕様は依頼文に準拠。

- 監視: FTP/FTPS を 60 秒ごとにポーリング
- 判定: 新規かつ 30 秒以上サイズ変化なしで通知
- 永続化: SQLite (`data/monitor.db`)
- 通知: Windows トースト通知
- ログ: `logs/monitor_YYYY-MM-DD.log`
