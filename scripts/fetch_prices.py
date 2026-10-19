"""東証上場銘柄の約1年分の終値を取得して data/prices_all.json に保存する（GitHub Actions で実行）。

銘柄リスト：codes.json ＋ JPX の「東証上場銘柄一覧」（内国株式）。
株価：Yahoo!ファイナンスの日足（取れない銘柄は stooq で再試行）。無料。
結果の要約は data/fetch_log.txt に書く（失敗の原因もここで分かる）。
取れなかった銘柄は前回の値を残す。
"""
import io
import json
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd
from curl_cffi import requests

JST = timezone(timedelta(hours=9))
JPX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
OUT = "data/prices_all.json"
LOG = "data/fetch_log.txt"
log_lines = []
errors = {}


def log(msg):
    print(msg, flush=True)
    log_lines.append(msg)


def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, impersonate="chrome", timeout=30)
            if r.status_code == 429:
                last = "429"
                time.sleep(5 * (i + 1))
                continue
            return r
        except Exception as e:
            last = repr(e)
            time.sleep(2 * (i + 1))
    raise RuntimeError(last or "unknown")


def load_codes():
    codes = set(json.load(open("codes.json", encoding="utf-8")))
    log(f"codes.json：{len(codes)}銘柄")
    try:
        r = get(JPX_URL)
        df = pd.read_excel(io.BytesIO(r.content), dtype=str)
        df = df[df["市場・商品区分"].str.contains("内国株式", na=False)]
        codes |= set(df["コード"].str.strip())
        log(f"JPX一覧：内国株式 {len(df)}銘柄")
    except Exception as e:
        log(f"JPX一覧を取得できず（codes.json のみ使用）：{e!r}")
    return sorted(c for c in codes if c and len(c) == 4)


def yahoo(code):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.T?range=13mo&interval=1d"
    r = get(url)
    if r.status_code != 200:
        raise RuntimeError(f"yahoo HTTP {r.status_code}")
    res = r.json()["chart"]["result"][0]
    ts = res.get("timestamp") or []
    close = res["indicators"]["quote"][0]["close"]
    s = {}
    for t, v in zip(ts, close):
        if v is not None:
            s[datetime.fromtimestamp(t, JST).strftime("%Y/%m/%d")] = round(float(v), 1)
    if not s:
        raise RuntimeError("yahoo 空データ")
    return s


def stooq(code):
    r = get(f"https://stooq.com/q/d/l/?s={code.lower()}.jp&i=d")
    if r.status_code != 200 or not r.text.startswith("Date"):
        raise RuntimeError(f"stooq {r.status_code} {r.text[:40]!r}")
    df = pd.read_csv(io.StringIO(r.text)).tail(280)
    s = {d.replace("-", "/"): round(float(c), 1) for d, c in zip(df["Date"], df["Close"])}
    if not s:
        raise RuntimeError("stooq 空データ")
    return s


def fetch_one(code):
    try:
        return code, yahoo(code), "yahoo"
    except Exception as e1:
        try:
            return code, stooq(code), "stooq"
        except Exception as e2:
            errors[code] = f"{e1} / {e2}"
            return code, None, None


def main():
    started = time.time()
    prev = {}
    if os.path.exists(OUT):
        try:
            p = json.load(open(OUT, encoding="utf-8"))
            prev = {c: dict(zip(p["dates"], arr)) for c, arr in p["close"].items()}
        except Exception as e:
            log(f"前回ファイルを読めず：{e!r}")
    codes = load_codes()
    series, src_count = {}, {"yahoo": 0, "stooq": 0}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for i, (code, s, src) in enumerate(ex.map(fetch_one, codes), 1):
            if s:
                series[code] = s
                src_count[src] += 1
            if i % 250 == 0:
                log(f"{i}/{len(codes)} 処理済み（取得 {len(series)}）")
    log(f"今回取得：{len(series)}/{len(codes)}銘柄（yahoo {src_count['yahoo']}、stooq {src_count['stooq']}）")
    kept = 0
    for c, s in prev.items():  # 取れなかった銘柄は前回の値を残す
        if c not in series:
            series[c] = {d: v for d, v in s.items() if v is not None}
            kept += 1
        else:
            for d, v in s.items():
                if v is not None:
                    series[c].setdefault(d, v)
    if kept:
        log(f"前回の値を残した銘柄：{kept}")
    if errors:
        log("取得できなかった例：" + "；".join(f"{c} {m}" for c, m in list(errors.items())[:15]))
    if not series:
        log("1銘柄も取得できなかったため、prices_all.json は更新しません")
        return
    cnt = {}
    for s in series.values():
        for d in s:
            cnt[d] = cnt.get(d, 0) + 1
    need = max(1, len(series) * 0.3)
    dates = sorted(d for d, n in cnt.items() if n >= need)[-260:]  # 祝日などの空行を除く
    out = {
        "updated": datetime.now(JST).strftime("%Y/%m/%d %H:%M JST"),
        "source": "Yahoo!ファイナンス（一部 stooq）の日足終値（株式分割調整済み）。GitHub Actions で毎営業日16:45 JSTに取得",
        "dates": dates,
        "close": {c: [s.get(d) for d in dates] for c, s in sorted(series.items())},
    }
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"保存：{len(out['close'])}銘柄、{dates[0]}〜{dates[-1]}（{len(dates)}営業日）、{time.time() - started:.0f}秒")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("エラー：" + traceback.format_exc())
    finally:
        os.makedirs("data", exist_ok=True)
        with open(LOG, "w", encoding="utf-8") as f:
            f.write(datetime.now(JST).strftime("%Y/%m/%d %H:%M JST") + "\n" + "\n".join(log_lines) + "\n")
