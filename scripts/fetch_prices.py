"""東証上場銘柄の約1年分の終値を取得して data/prices_all.json に保存する（GitHub Actions で実行）。
あわせて、codes_ohlcv.json の銘柄（プライム・グロース）と日経平均の直近60営業日の
始値・高値・安値・終値・出来高を data/ohlcv_recent.json に保存する（invest_v3 の②⑦が読む。2026-10-25追加）。

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
OUT_OHLCV = "data/ohlcv_recent.json"
OHLCV_DAYS = 60
OHLCV_CODES = "codes_ohlcv.json"
INDEX_CODES = ["^N225"]
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


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def yahoo(code):
    sym = code if code.startswith("^") else f"{code}.T"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=13mo&interval=1d"
    r = get(url)
    if r.status_code != 200:
        raise RuntimeError(f"yahoo HTTP {r.status_code}")
    res = r.json()["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    close = q["close"]
    o, h, lo, v = q.get("open") or [], q.get("high") or [], q.get("low") or [], q.get("volume") or []
    s, ohlcv = {}, {}
    for i, (t, c) in enumerate(zip(ts, close)):
        if c is None:
            continue
        d = datetime.fromtimestamp(t, JST).strftime("%Y/%m/%d")
        s[d] = round(float(c), 1)
        ohlcv[d] = [_r(o[i] if i < len(o) else None), _r(h[i] if i < len(h) else None),
                    _r(lo[i] if i < len(lo) else None), _r(c), int(v[i]) if i < len(v) and v[i] is not None else None]
    if not s:
        raise RuntimeError("yahoo 空データ")
    return s, ohlcv


def stooq(code):
    r = get(f"https://stooq.com/q/d/l/?s={code.lower()}.jp&i=d")
    if r.status_code != 200 or not r.text.startswith("Date"):
        raise RuntimeError(f"stooq {r.status_code} {r.text[:40]!r}")
    df = pd.read_csv(io.StringIO(r.text)).tail(280)
    s = {d.replace("-", "/"): round(float(c), 1) for d, c in zip(df["Date"], df["Close"])}
    if not s:
        raise RuntimeError("stooq 空データ")
    ohlcv = {}
    if {"Open", "High", "Low", "Volume"} <= set(df.columns):
        for row in df.itertuples(index=False):
            ohlcv[row.Date.replace("-", "/")] = [_r(row.Open), _r(row.High), _r(row.Low), _r(row.Close),
                                                 int(row.Volume) if pd.notna(row.Volume) else None]
    return s, ohlcv


def fetch_one(code):
    try:
        s, o = yahoo(code)
        return code, s, "yahoo", o
    except Exception as e1:
        if code.startswith("^"):
            errors[code] = f"{e1}"
            return code, None, None, None
        try:
            s, o = stooq(code)
            return code, s, "stooq", o
        except Exception as e2:
            errors[code] = f"{e1} / {e2}"
            return code, None, None, None


def load_ohlcv_codes():
    try:
        d = json.load(open(OHLCV_CODES, encoding="utf-8"))
        codes = sorted(set(d.get("prime", [])) | set(d.get("growth", [])))
        log(f"{OHLCV_CODES}：{len(codes)}銘柄（4本値・出来高を保存）")
        return codes
    except Exception as e:
        log(f"{OHLCV_CODES} を読めず（4本値は保存しない）：{e!r}")
        return []


def save_ohlcv(ohlcv_all, ohlcv_codes):
    """直近 OHLCV_DAYS 営業日の4本値・出来高を保存する。取れなかった銘柄は入れない（前回の値は残さない）"""
    targets = [c for c in ohlcv_codes + INDEX_CODES if ohlcv_all.get(c)]
    if not targets:
        log("4本値：1銘柄も取得できなかったため、ohlcv_recent.json は更新しません")
        return
    cnt = {}
    for c in targets:
        for d in ohlcv_all[c]:
            cnt[d] = cnt.get(d, 0) + 1
    need = max(1, len(targets) * 0.3)
    dates = sorted(d for d, n in cnt.items() if n >= need)[-OHLCV_DAYS:]
    out = {
        "updated": datetime.now(JST).strftime("%Y/%m/%d %H:%M JST"),
        "source": "Yahoo!ファイナンス（一部 stooq）の日足。株式分割調整済み（配当調整なし）",
        "fields": ["open", "high", "low", "close", "volume"],
        "dates": dates,
        "data": {c: [ohlcv_all[c].get(d) for d in dates] for c in sorted(targets)},
    }
    with open(OUT_OHLCV, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"4本値の保存：{len(targets)}/{len(ohlcv_codes) + len(INDEX_CODES)}銘柄、{dates[0]}〜{dates[-1]}（{len(dates)}営業日）")


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
    ohlcv_codes = load_ohlcv_codes()
    close_set = set(codes)
    fetch_list = sorted(close_set | set(ohlcv_codes)) + INDEX_CODES
    series, src_count, ohlcv_all = {}, {"yahoo": 0, "stooq": 0}, {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for i, (code, s, src, o) in enumerate(ex.map(fetch_one, fetch_list), 1):
            if o:
                ohlcv_all[code] = o
            if s and code in close_set:
                series[code] = s
                src_count[src] += 1
            if i % 500 == 0:
                log(f"{i}/{len(fetch_list)} 処理済み（終値 {len(series)}、4本値 {len(ohlcv_all)}）")
    log(f"今回取得：{len(series)}/{len(codes)}銘柄（yahoo {src_count['yahoo']}、stooq {src_count['stooq']}）")
    try:
        save_ohlcv(ohlcv_all, ohlcv_codes)
    except Exception:
        log("4本値の保存でエラー（終値の保存は続ける）：" + traceback.format_exc())
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
        log(f"取得できなかった銘柄：{len(errors)}。例：" + "；".join(f"{c} {m[:60]}" for c, m in list(errors.items())[:15]))
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
