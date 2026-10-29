"""codes.json の銘柄の財務指標（時価総額・PER・ROE・営業利益率・直近四半期の営業増益率）を
Yahoo!ファイナンス（yfinance）から取得して data/fundamentals.json に保存する（GitHub Actions で実行、AIは使わない）。

出力：
{"updated": "YYYY/MM/DD HH:MM JST", "source": "...",
 "fields": ["mcap_oku", "per", "roe", "om", "oi_yoy", "q_end"],
 "data": {"6857": [時価総額(億円), PER(倍・実績), ROE(%), 営業利益率(%), 営業増益率(%・直近四半期の前年同期比), 直近四半期の末日], ...}}
取れない値は null。結果の要約と失敗理由は data/fundamentals_log.txt に書く。
"""
import json, os, time, traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import yfinance as yf

JST = timezone(timedelta(hours=9))
OUT = "data/fundamentals.json"
LOG = "data/fundamentals_log.txt"
log_lines, errors = [], {}


def log(msg):
    print(msg, flush=True)
    log_lines.append(msg)


def num(x, nd=2):
    try:
        v = float(x)
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return round(v, nd)
    except Exception:
        return None


def oi_yoy(t):
    """直近四半期の営業利益と、その4四半期前（前年同期）を比べる。"""
    try:
        q = t.quarterly_income_stmt
        if q is None or q.empty or "Operating Income" not in q.index:
            return None, None
        s = q.loc["Operating Income"].dropna()
        s = s.sort_index(ascending=False)
        if len(s) < 5:
            return None, str(s.index[0].date()) if len(s) else None
        cur, prev = float(s.iloc[0]), float(s.iloc[4])
        end = s.index[0].strftime("%Y/%m/%d")
        if prev <= 0:
            return None, end  # 前年同期が赤字・ゼロなら率は出さない
        return round((cur / prev - 1) * 100, 1), end
    except Exception:
        return None, None


def fetch_one(code):
    for attempt in range(3):
        try:
            t = yf.Ticker(f"{code}.T")
            info = t.info or {}
            mcap = info.get("marketCap")
            per = info.get("trailingPE")
            roe = info.get("returnOnEquity")
            om = info.get("operatingMargins")
            yoy, qend = oi_yoy(t)
            row = [num(mcap / 1e8, 0) if mcap else None, num(per, 1),
                   num(roe * 100, 1) if roe is not None else None,
                   num(om * 100, 1) if om is not None else None, yoy, qend]
            return code, row
        except Exception as e:
            msg = repr(e)
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
            else:
                errors[code] = msg[:120]
    return code, None


def main():
    started = time.time()
    codes = sorted(c for c in json.load(open("codes.json", encoding="utf-8")) if c and len(c) == 4)
    log(f"codes.json：{len(codes)}銘柄")
    prev = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT, encoding="utf-8")).get("data", {})
        except Exception:
            pass
    data, ok = {}, 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        for i, (code, row) in enumerate(ex.map(fetch_one, codes), 1):
            if row and any(v is not None for v in row[:5]):
                data[code] = row
                ok += 1
            elif code in prev:
                data[code] = prev[code]  # 取れなかった銘柄は前回の値を残す
            if i % 200 == 0:
                log(f"{i}/{len(codes)} 処理済み（取得 {ok}）")
            time.sleep(0.2)
    log(f"今回取得：{ok}/{len(codes)}銘柄、保存 {len(data)}銘柄、{time.time() - started:.0f}秒")
    if errors:
        log(f"取得できなかった銘柄：{len(errors)}。例：" + "；".join(f"{c} {m}" for c, m in list(errors.items())[:10]))
    if not data:
        log("1銘柄も取得できなかったため保存しません")
        return
    out = {
        "updated": datetime.now(JST).strftime("%Y/%m/%d %H:%M JST"),
        "source": "Yahoo!ファイナンス（yfinance）。時価総額・PER（実績）・ROE・営業利益率は直近の会社データ、営業増益率は直近四半期の営業利益の前年同期比",
        "fields": ["mcap_oku", "per", "roe", "om", "oi_yoy", "q_end"],
        "data": data,
    }
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("エラー：" + traceback.format_exc())
    finally:
        os.makedirs("data", exist_ok=True)
        with open(LOG, "w", encoding="utf-8") as f:
            f.write(datetime.now(JST).strftime("%Y/%m/%d %H:%M JST") + "\n" + "\n".join(log_lines) + "\n")
