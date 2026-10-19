"""東証上場銘柄の約1年分の終値を取得して data/prices_all.json に保存する（GitHub Actions で実行）。

銘柄リスト：JPX の「東証上場銘柄一覧」（内国株式のみ）。取れないときは codes.json を使う。
株価：Yahoo!ファイナンス（yfinance 経由、無料）。終値は株式分割を調整済みの値。
"""
import io
import json
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import yfinance as yf

JST = timezone(timedelta(hours=9))
JPX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
CHUNK = 100


def load_codes():
    codes = set(json.load(open("codes.json", encoding="utf-8")))
    try:
        r = requests.get(JPX_URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        df = pd.read_excel(io.BytesIO(r.content), dtype=str)
        df = df[df["市場・商品区分"].str.contains("内国株式", na=False)]
        codes |= set(df["コード"].str.strip())
        print(f"JPX一覧：{len(df)}銘柄")
    except Exception as e:  # 一覧が取れなくても codes.json だけで続ける
        print(f"JPX一覧を取得できませんでした（codes.json のみ使用）：{e}")
    return sorted(c for c in codes if c and len(c) == 4)


def fetch(codes):
    closes = {}
    for i in range(0, len(codes), CHUNK):
        part = codes[i:i + CHUNK]
        tickers = [c + ".T" for c in part]
        for attempt in range(3):
            try:
                df = yf.download(tickers, period="13mo", interval="1d", auto_adjust=False,
                                 group_by="ticker", threads=True, progress=False)
                break
            except Exception as e:
                print(f"再試行 {attempt + 1}：{e}")
                time.sleep(10)
        else:
            continue
        for c, t in zip(part, tickers):
            try:
                s = df[t]["Close"] if len(tickers) > 1 else df["Close"]
            except Exception:
                continue
            s = s.dropna()
            if len(s):
                closes[c] = s
        print(f"{min(i + CHUNK, len(codes))}/{len(codes)} 取得済み（有効 {len(closes)}）")
        time.sleep(1)
    return closes


def main():
    codes = load_codes()
    closes = fetch(codes)
    if len(closes) < len(codes) * 0.5:
        print("取得できた銘柄が半分未満のため、前回のファイルを残して終了します")
        sys.exit(1)
    frame = pd.DataFrame(closes).sort_index()
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    frame = frame[frame.notna().sum(axis=1) >= len(closes) * 0.3]  # 祝日などの空行を除く
    frame = frame.tail(260)
    dates = [d.strftime("%Y/%m/%d") for d in frame.index]
    out = {
        "updated": datetime.now(JST).strftime("%Y/%m/%d %H:%M JST"),
        "source": "Yahoo!ファイナンス（yfinance）の日足終値（株式分割調整済み）。GitHub Actions で毎営業日16:45 JSTに取得",
        "dates": dates,
        "close": {c: [None if pd.isna(v) else round(float(v), 1) for v in frame[c].tolist()] for c in frame.columns},
    }
    with open("data/prices_all.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"保存：{len(out['close'])}銘柄、{dates[0]}〜{dates[-1]}（{len(dates)}営業日）")


if __name__ == "__main__":
    main()
