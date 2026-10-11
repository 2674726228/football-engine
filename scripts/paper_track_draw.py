#!/usr/bin/env python3
"""AUT 平局候选策略纸面跟踪（2026-10-12 起）

假设: 奥地利 Bundesliga 收盘隐含平局概率 ∈ [0.15,0.25) 时，按收盘平局赔率
下注 1u 为正 EV（历史 EV@收盘 +7.5%，n=969；见 _vault/close_calibration_notes.md）。

数据: data/state/fdc/new_AUT.csv（football-data /new/ 格式，收盘列
      PSC→B365C→MaxC→AvgC 级联取基准；当前赛季 PSC 回填滞后故需级联）。
每日 auto_run 自动运行；live ROI 低于 -15% 且 n≥80 时告警失效。
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime as _dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FDC = ROOT / "data" / "state" / "fdc" / "new_AUT.csv"
OUT = ROOT / "data" / "state" / "paper_track_draw.json"
BENCHES = ("PSC", "B365C", "MaxC", "AvgC")


def main() -> int:
    tracked = {}
    if OUT.exists():
        try:
            tracked = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            tracked = {}
    ledger = tracked.setdefault("bets", {})

    with open(FDC, encoding="utf-8-sig", errors="ignore") as fh:
        for r in csv.DictReader(fh):
            raw_date = r.get("Date", "")
            try:
                date = _dt.strptime(raw_date, "%d/%m/%Y").strftime("%Y-%m-%d")
            except Exception:
                continue
            ftr = (r.get("Res") or "").strip()
            if ftr not in ("H", "D", "A"):
                continue
            bench, bench_name = None, None
            for pk in BENCHES:
                try:
                    trio = (float(r.get(pk + "H") or 0), float(r.get(pk + "D") or 0),
                            float(r.get(pk + "A") or 0))
                except (TypeError, ValueError):
                    continue
                if min(trio) > 1.0:
                    bench, bench_name = trio, pk
                    break
            if not bench:
                continue
            inv = [1 / x for x in bench]
            s = sum(inv)
            p_draw = inv[1] / s
            if not (0.15 <= p_draw < 0.25):
                continue
            key = f"{date}_{r.get('Home')}_{r.get('Away')}"
            if key in ledger:
                continue
            ledger[key] = {
                "date": date, "match": f"{r.get('Home')} vs {r.get('Away')}",
                "benchmark": bench_name, "close_d": bench[1],
                "p_draw": round(p_draw, 4), "hit": ftr == "D",
                "pnl": (bench[1] - 1) if ftr == "D" else -1,
            }

    bets = list(ledger.values())
    n = len(bets)
    hits = sum(1 for b in bets if b["hit"])
    pnl = sum(b["pnl"] for b in bets)
    by_year = defaultdict(lambda: [0, 0, 0.0])
    for b in bets:
        y = b["date"][:4]
        by_year[y][0] += 1
        by_year[y][1] += 1 if b["hit"] else 0
        by_year[y][2] += b["pnl"]
    out = {
        "updated_at": _dt.now().strftime("%Y-%m-%d"),
        "n": n, "hits": hits,
        "hit_rate": round(hits / n, 4) if n else None,
        "pnl_u": round(pnl, 2), "roi": round(pnl / n, 4) if n else None,
        "by_year": {y: {"n": v[0], "hit_rate": round(v[1] / v[0], 3), "pnl_u": round(v[2], 1)}
                    for y, v in sorted(by_year.items())},
        "bets": ledger,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"纸面跟踪: {n} 注, 中 {hits} ({out['hit_rate'] if n else '-'}) "
          f"ROI {out['roi'] if n else '-'}")
    if n >= 80 and out["roi"] is not None and out["roi"] < -0.15:
        print("⚠ live ROI 显著为负——候选假设失效，停止跟踪")
    return 0


if __name__ == "__main__":
    sys.exit(main())
