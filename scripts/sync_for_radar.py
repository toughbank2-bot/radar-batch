"""prices_all.json から、分析ページ用の data/prices.json を作る（Claude の作業フォルダで実行）。

使い方：python3 sync_for_radar.py prices_all.json data/members.json data/prices.json data/stocks_nm.json
対象：members.json の全銘柄と stocks_nm.json の全銘柄。
"""
import json
import sys


def main():
    src, members_p, out_p, nm_p = sys.argv[1:5]
    pa = json.load(open(src, encoding="utf-8"))
    members = json.load(open(members_p, encoding="utf-8"))
    nm = json.load(open(nm_p, encoding="utf-8"))
    targets = sorted({r[1] for r in members} | set(nm))
    dates = pa["dates"][-260:]
    n = len(dates)
    close, chg1d, missing = {}, {}, []
    for c in targets:
        arr = pa["close"].get(c)
        if not arr:
            missing.append(c)
            close[c] = [None] * n
            continue
        arr = arr[-n:]
        close[c] = arr
        if len(arr) >= 2 and arr[-1] and arr[-2]:
            chg1d[c] = round((arr[-1] / arr[-2] - 1) * 100, 2)
    out = {
        "dates": dates,
        "close": close,
        "chg1d": chg1d,
        "targets": targets,
        "source": f"{pa.get('source', '')}（更新 {pa.get('updated', '')}）",
    }
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"最終日：{dates[-1]}")
    print(f"バッチ更新：{pa.get('updated', '不明')}")
    print(f"対象 {len(targets)}社、最終日の終値あり {sum(1 for c in targets if close[c][-1])}社、前日比あり {len(chg1d)}社")
    if missing:
        print(f"prices_all.json にない銘柄 {len(missing)}社：{' '.join(missing[:50])}")


if __name__ == "__main__":
    main()
