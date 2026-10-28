#!/usr/bin/env python3
"""下注级 CLV · NJStats 多庄收盘基准（2026-10-12，试用窗口产物）

NJStats 归档的 oddsInfo 含 8 家博彩公司的开盘/收盘欧赔（36*/平*/*/威*/Inter*/
皇*/18*/澳*），带赛果——是继 football-data 之后的第三个独立收盘基准。
核心用途：CLV 的"基准锐度梯度"检验——澳彩(软)→平博(锐)逐步变差，
证明结构水位逆风，且量化"用软庄量 CLV 会高估自己"。
"""
import json, glob, statistics as st
from collections import defaultdict

def main():
    close = defaultdict(dict)
    seen = set()
    for fn in sorted(glob.glob('data/state/njstats_archive/*_odds*.json')):
        for r in json.load(open(fn, encoding="utf-8"))['data']:
            key = (r.get('matchTimeStr', '')[:10], r.get('homeName'), r.get('awayName'))
            if key in seen or not key[0]:
                continue
            seen.add(key)
            for oi in (r.get('oddsInfo') or []):
                eu = oi.get('europeOdds') or {}
                try:
                    trio = (float(eu['closingHome']), float(eu['closingDraw']), float(eu['closingAway']))
                except Exception:
                    continue
                if min(trio) > 1.0:
                    close[key][oi['cName']] = trio
    rows = [json.loads(l) for l in open('data/state/review_ledger.jsonl', encoding="utf-8")]
    dates = sorted({k[0] for k in close})
    bets = [r for r in rows if r.get('best_selection') is not None and r.get('date') in dates]
    sel_fields = ("home_odds", "draw_odds", "away_odds")
    cache, res = {}, defaultdict(list)
    for b in bets:
        date = b['date']
        if date not in cache:
            try:
                d = json.load(open(f'data/daily/{date}/predictions.json', encoding="utf-8"))
                preds = d.get('predictions', d) if isinstance(d, dict) else d
                cache[date] = {p.get('match_id'): p for p in preds}
            except Exception:
                cache[date] = {}
        pred = cache[date].get(b.get('match_id'))
        if not pred:
            continue
        sel = int(b['best_selection'])
        taken = pred.get(sel_fields[sel])
        if not taken or taken <= 1.0:
            continue
        hit = next((k for k in close if k[0] == date
                    and pred.get('home_team') in k[1] and pred.get('away_team') in k[2]), None)
        if not hit:
            continue
        for book, trio in close[hit].items():
            res[book].append(taken / trio[sel] - 1)
    out = {book: {"n": len(cl), "mean": round(st.mean(cl), 4),
                  "median": round(st.median(cl), 4),
                  "pos_pct": round(sum(1 for x in cl if x > 0) / len(cl), 3)}
           for book, cl in res.items() if len(cl) >= 5}
    print(json.dumps({"books": len(close), "bets_matched": len(res.get(next(iter(res), ''), [])),
                      "clv_by_benchmark": out}, ensure_ascii=False, indent=1))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
