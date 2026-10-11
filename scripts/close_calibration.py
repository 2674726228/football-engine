#!/usr/bin/env python3
"""收盘市场校准研究（2026-10-12 过夜大件）

数据: data/state/fdc/parsed.jsonl（60,862 场，含 B365/Pinnacle/Max/Avg 收盘价 + 赛果）
回答三个战略问题（全程只用收盘价与赛果，免费且可永久复算）:
1. 收盘校准曲线: Pinnacle 收盘隐含概率 vs 实际赛果频率，按概率带/联赛分桶
   —— 找收盘市场自身定价失准的分段
2. 软庄分歧信号: B365 收盘 vs Pinnacle 收盘的隐含概率差（娱乐资金偏误）
   —— 分歧大的场次，追随 Pinnacle 方向是否有正 ROI（免费的可执行信号）
3. 平局错价研究: 平局在各收盘赔率段的实际胜率 vs 隐含
产出: data/state/close_calibration.json + _vault/close_calibration_notes.md
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FDC = ROOT / "data" / "state" / "fdc" / "parsed.jsonl"
OUT = ROOT / "data" / "state" / "close_calibration.json"
VAULT = ROOT / "_vault" / "close_calibration_notes.md"

LEAGUE_GROUP = {
    "五大": ["D1", "E0", "SP1", "I1", "F1"],
    "次级": ["D2", "E1", "SP2", "I2", "F2", "N1", "P1"],
    "日韩中": ["JPN", "CHN"],
    "南美": ["BRA", "ARG", "MEX"],
    "北欧": ["FIN", "NOR", "SWE"],
    "其他欧": ["SC0", "G1", "T1", "SW1", "AUT"],
    "美职": ["USA"],
}


def group_of(src: str) -> str:
    div = src.split("_", 1)[1] if "_" in src else src
    for g, divs in LEAGUE_GROUP.items():
        if div in divs:
            return g
    return "其他"


def devig_3(h: float, d: float, a: float) -> tuple[float, float, float] | None:
    if min(h, d, a) <= 1.0:
        return None
    inv = [1 / h, 1 / d, 1 / a]
    s = sum(inv)
    return tuple(x / s for x in inv)


def outcome_idx(hg, ag) -> int:
    return 0 if hg > ag else (1 if hg == ag else 2)


def bucket_of(p: float) -> str:
    for lo, hi in ((0, .15), (.15, .25), (.25, .35), (.35, .45), (.45, .55),
                   (.55, .65), (.65, .75), (.75, .85), (.85, 1.01)):
        if lo <= p < hi:
            return f"[{lo:.2f},{hi:.2f})"
    return "[.85,1.01)"


def main() -> int:
    rows = []
    for line in open(FDC, encoding="utf-8"):
        r = json.loads(line)
        hg, ag = r.get("hg"), r.get("ag")
        if hg is None or ag is None:
            continue
        psc = devig_3(r.get("psc_h") or 0, r.get("psc_d") or 0, r.get("psc_a") or 0)
        b365c = devig_3(r.get("b365c_h") or 0, r.get("b365c_d") or 0, r.get("b365c_a") or 0)
        if not psc and not b365c:
            continue
        rows.append({**r, "oi": outcome_idx(hg, ag), "pscp": psc, "b365p": b365c,
                     "group": group_of(r["src"])})
    print(f"可用场次: {len(rows)}")

    # ---- 1. Pinnacle 收盘校准曲线（按概率带 × 组群）
    calib: dict = defaultdict(lambda: {"n": 0, "wins": 0, "p_sum": 0.0})
    for r in rows:
        if not r["pscp"]:
            continue
        for i, p in enumerate(r["pscp"]):
            key = (r["group"], "hda"[i], bucket_of(p))
            c = calib[key]
            c["n"] += 1
            c["p_sum"] += p
            if r["oi"] == i:
                c["wins"] += 1
    calibration = []
    for (g, pos, bucket), c in sorted(calib.items()):
        if c["n"] < 100:
            continue
        implied = c["p_sum"] / c["n"]
        actual = c["wins"] / c["n"]
        calibration.append({
            "group": g, "outcome": pos, "bucket": bucket, "n": c["n"],
            "implied": round(implied, 4), "actual": round(actual, 4),
            "edge": round(actual - implied, 4),
        })

    # ---- 2. 软庄分歧信号（B365C vs PSC，双方都有的行）
    diverge_stat = defaultdict(lambda: {"n": 0, "bets": 0, "wins": 0, "profit": 0.0})
    for r in rows:
        if not (r["pscp"] and r["b365p"]):
            continue
        for i in range(3):
            diff = r["b365p"][i] - r["pscp"][i]
            if abs(diff) < 0.02:
                continue
            b = "b365高" if diff > 0 else "b365低"
            key = (r["group"], "hda"[i], b, min(4, int(abs(diff) * 20)))  # 差距 0.05 一档
            c = diverge_stat[key]
            c["n"] += 1
            # 策略：B365 定价高于 Pinnacle → 娱乐资金推高了该结果的价格 → 追 Pinnacle 反方向？
            # 更直接：按 Pinnacle 收盘价下注该结果（若 win 按收盘赔付）
            close_odds = [r.get("psc_h"), r.get("psc_d"), r.get("psc_a")][i]
            if close_odds and close_odds > 1.01:
                c["bets"] += 1
                if r["oi"] == i:
                    c["wins"] += 1
                    c["profit"] += close_odds - 1
                else:
                    c["profit"] += -1
    divergence = []
    for (g, pos, side, band), c in sorted(diverge_stat.items()):
        if c["bets"] < 150:
            continue
        divergence.append({
            "group": g, "outcome": pos, "side": side, "band": band,
            "n": c["n"], "bets": c["bets"],
            "hit": round(c["wins"] / c["bets"], 4),
            "roi": round(c["profit"] / c["bets"], 4),
        })

    # ---- 3. 平局错价（Pinnacle 收盘平局赔率带 × 组群）
    draw_study = defaultdict(lambda: {"n": 0, "draws": 0, "odds_sum": 0.0})
    for r in rows:
        if not r["pscp"]:
            continue
        do = r.get("psc_d")
        p = r["pscp"][1]
        if not do or do <= 1.0:
            continue
        key = (r["group"], bucket_of(p))
        c = draw_study[key]
        c["n"] += 1
        c["odds_sum"] += do
        if r["oi"] == 1:
            c["draws"] += 1
    draws = []
    for (g, bucket), c in sorted(draw_study.items()):
        if c["n"] < 150:
            continue
        implied = (c["odds_sum"] / c["n"])
        implied_p = 1 / implied
        actual = c["draws"] / c["n"]
        draws.append({
            "group": g, "bucket": bucket, "n": c["n"],
            "avg_close_odds": round(implied, 3),
            "implied_p": round(implied_p, 4), "actual_p": round(actual, 4),
            "edge": round(actual - implied_p, 4),
            "ev_at_close": round(actual * implied - 1, 4),
        })

    report = {
        "generated_at": "2026-10-12",
        "n_matches": len(rows),
        "calibration": calibration,
        "divergence_signals": sorted(divergence, key=lambda x: x["roi"]),
        "draw_study": draws,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    # 摘要打印
    print("\n== 校准最失准分段（|edge|>2%，n≥300）==")
    for c in sorted(calibration, key=lambda x: -abs(x["edge"])):
        if c["n"] >= 300 and abs(c["edge"]) > 0.02:
            print(f"  {c['group']:4s} {c['outcome']} {c['bucket']} n={c['n']:5d} "
                  f"隐含 {c['implied']:.3f} 实际 {c['actual']:.3f} edge {c['edge']:+.3f}")
    print("\n== 软庄分歧 ROI（n≥300，按 ROI 排序前后各5）==")
    dd = sorted(divergence, key=lambda x: x["roi"])
    for c in dd[:5]:
        print(f"  {c['group']:4s} {c['outcome']} {c['side']} band{c['band']} bets={c['bets']:4d} ROI {c['roi']:+.3f}")
    print("  ...")
    for c in dd[-5:]:
        print(f"  {c['group']:4s} {c['outcome']} {c['side']} band{c['band']} bets={c['bets']:4d} ROI {c['roi']:+.3f}")
    print("\n== 平局 EV（n≥400）==")
    for c in sorted(draws, key=lambda x: x["ev_at_close"]):
        if c["n"] >= 400:
            print(f"  {c['group']:4s} {c['bucket']} n={c['n']:5d} 平局均赔 {c['avg_close_odds']:.2f} "
                  f"EV@收盘 {c['ev_at_close']:+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
