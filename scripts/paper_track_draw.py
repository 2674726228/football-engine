#!/usr/bin/env python3
"""AUT 平局候选策略纸面跟踪（2026-10-12 起）

假设: 奥地利 Bundesliga 收盘隐含平局概率 ∈ [0.15,0.25) 时，按收盘平局赔率
下注 1u 为正 EV（历史 EV@收盘 +7.5%，n=969；见 _vault/close_calibration_notes.md）。

每日运行（fdc CSV 由 fetch_football_data.py 刷新后）:
- 扫描最新入库的 AUT 场次，closing 隐含 ∈ [0.15,0.25) 且有赛果 → 记一笔
- 输出累计战绩到 data/state/paper_track_draw.json
- 与假设偏离显著（live ROI 低于 -15% 且 n≥80）时打印告警
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FDC_DIR = ROOT / "data" / "state" / "fdc"
OUT = ROOT / "data" / "state" / "paper_track_draw.json"


def main() -> int:
    tracked = {}
    if OUT.exists():
        try:
            tracked = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            tracked = {}
    ledger = tracked.setdefault("bets", {})

    for f in sorted(FDC_DIR.glob("2627_AUT.csv")) + sorted(FDC_DIR.glob("2526_AUT.csv")):
        import csv
        with open(f, encoding="utf-8-sig", errors="ignore") as fh:
            for r in csv.DictReader(fh):
                date = r.get("Date", "")
                try:
                    pscd = float(r.get("PSCD") or 0)
                except ValueError:
                    continue
                if pscd <= 1.0:
                    continue
                inv = [1 / float(r.get("PSCH") or 0), 1 / pscd, 1 / float(r.get("PSCA") or 0)]
                if min(inv) <= 0:
                    continue
                s = sum(inv)
                p_draw = inv[1] / s
                if not (0.15 <= p_draw < 0.25):
                    continue
                ftr = r.get("FTR", "")
                if ftr not in ("H", "D", "A"):
                    continue
                key = f"{date}_{r.get('HomeTeam')}_{r.get('AwayTeam')}"
                if key in ledger:
                    continue
                ledger[key] = {
                    "date": date, "match": f"{r.get('HomeTeam')} vs {r.get('AwayTeam')}",
                    "pscd": pscd, "p_draw": round(p_draw, 4),
                    "hit": ftr == "D",
                    "pnl": (pscd - 1) if ftr == "D" else -1,
                }

    bets = list(ledger.values())
    n = len(bets)
    hits = sum(1 for b in bets if b["hit"])
    pnl = sum(b["pnl"] for b in bets)
    out = {
        "updated_at": "2026-10-12",
        "n": n, "hits": hits, "hit_rate": round(hits / n, 4) if n else None,
        "pnl_u": round(pnl, 2), "roi": round(pnl / n, 4) if n else None,
        "bets": ledger,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"纸面跟踪: {n} 注, 中 {hits}, ROI {out['roi']}")
    if n >= 80 and out["roi"] is not None and out["roi"] < -0.15:
        print("⚠ live ROI 显著为负——候选假设失效，停止跟踪")
    return 0


if __name__ == "__main__":
    sys.exit(main())
