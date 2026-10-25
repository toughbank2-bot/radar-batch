# radar-batch
GitHub Actions で毎営業日 16:45 JST に株価を取得する。

| 出力 | 中身 | 使う側 |
|---|---|---|
| data/prices_all.json | codes.json の銘柄の約1年分の終値 | 製造業決算レーダー |
| data/ohlcv_recent.json | codes_ohlcv.json の銘柄（プライム・グロース）と日経平均の直近60営業日の始値・高値・安値・終値・出来高（2026-10-25追加） | invest_v3 の②テクニカル分析・⑦モメンタム分析（PCの update_ohlcv_daily.bat で10年分のデータに追記） |
| data/fetch_log.txt | 取得結果の要約 | 確認用 |

データ元は Yahoo!ファイナンス（一部 stooq）。株式分割調整済み、配当調整なし。無料。
