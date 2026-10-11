#!/usr/bin/env python3
"""下注级 CLV（Closing Line Value）持续报告（2026-10-11）

背景：#6 的 33,058 场门禁回放证明模型对收盘价无正 EV（odds_clv 全程为负），
但那是"模型全量回放"。本模块回答的是另一个更贴近实战的问题：
**我们实际下出去的每一注，价格上有没有跑赢收盘？**

定义（两个口径都给，防单口径误导）：
- clv_vs_close      = taken / closing_raw - 1     （vs DJYY 市场原始终盘价）
- clv_vs_fair_close = taken × fair_prob_close - 1 （vs 去水收盘公平价，行业惯例；
  竞彩 taken 价自带 ~8% 水位，vs 原始收盘会把水位误算成负 edge）

数据源：
- taken  ：data/daily/{date}/predictions.json 的 home/draw/away_odds（下注时点竞彩价）
- closing：data/state/odds_series/{date}_*.jsonl 的 euro 快照，取开赛前最后一条；
  只认 kickoff 前 36h 内的快照（再早不算"收盘"），并报告中位时距供判读

产出：data/state/clv_report.json（周检/auto_run 消费）。样本少时结论一律"样本不足"。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # engine/review/clv.py → 仓库根
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.beijing_time import beijing_today  # noqa: E402

DAILY = ROOT / "data" / "daily"
ODDS_SERIES = ROOT / "data" / "state" / "odds_series"
LEDGER = ROOT / "data" / "state" / "review_ledger.jsonl"
OUT = ROOT / "data" / "state" / "clv_report.json"

SELECTION_ODDS_FIELDS = ("home_odds", "draw_odds", "away_odds")
CLOSE_MAX_HORIZON_H = 36.0   # 快照距开赛超过 36h 不算收盘
MIN_BETS_FOR_VERDICT = 30    # 少于该样本数不给出 edge 结论


def devig(euro: dict) -> tuple[float, float, float]:
    """去水：三分赔率 → 公平概率 (h, d, a)。赔率非法时抛 ValueError。"""
    inv = []
    for k in ("home", "draw", "away"):
        o = float(euro[k])
        if o <= 1.0:
            raise ValueError(f"非法赔率 {k}={o}")
        inv.append(1.0 / o)
    s = sum(inv)
    return tuple(x / s for x in inv)


def clv_vs_close(taken: float, closing: float) -> float:
    """原始终盘口径：taken/closing - 1"""
    if taken <= 1.0 or closing <= 1.0:
        raise ValueError(f"赔率非法 taken={taken} closing={closing}")
    return taken / closing - 1.0


def clv_vs_fair_close(taken: float, euro_close: dict, sel: int) -> float:
    """去水收盘口径：taken × fair_prob - 1（sel: 0=主 1=平 2=客）"""
    fair = devig(euro_close)[sel]
    if taken <= 1.0:
        raise ValueError(f"赔率非法 taken={taken}")
    return taken * fair - 1.0


def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def pick_closing_snapshot(snapshots: list[dict], kickoff: datetime,
                          ) -> tuple[dict | None, float]:
    """返回 (开赛前最后一条快照, 时距小时)。无 36h 内快照返回 (None, inf)。"""
    best, best_gap = None, float("inf")
    for s in snapshots:
        try:
            ts = _parse_dt(s["ts"])
        except Exception:
            continue
        gap = (kickoff - ts).total_seconds() / 3600.0
        if 0 < gap <= CLOSE_MAX_HORIZON_H and gap < best_gap:
            best, best_gap = s, gap
    return best, best_gap


def load_closing_index(day: str) -> dict[str, list[dict]]:
    """{match_no: [快照按 ts 升序]}，来源为该日全部 odds_series 文件。"""
    idx: dict[str, list[dict]] = {}
    if not ODDS_SERIES.exists():
        return idx
    for f in sorted(ODDS_SERIES.glob(f"{day}_*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                snap = json.loads(line)
                no = snap.get("match_no")
                if no:
                    idx.setdefault(no, []).append(snap)
        except Exception:
            continue
    for no in idx:
        idx[no].sort(key=lambda s: s.get("ts", ""))
    return idx


def _match_no_of(match_id: str) -> str:
    # "2026-10-10_周六022" → "周六022"
    return match_id.split("_", 1)[1] if "_" in match_id else match_id


def collect_bets(days_back: int = 30) -> list[dict]:
    """已结算 + 有下注选择的行 → {date, match_id, sel, tier, taken}"""
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        sel = r.get("best_selection")
        if sel is None or not (0 <= int(sel) <= 2):
            continue
        out.append({
            "date": r.get("date", ""),
            "match_id": r.get("match_id", ""),
            "sel": int(sel),
            "tier": r.get("confidence_tier", ""),
        })
    return out


def build_report(days_back: int = 30) -> dict:
    bets = collect_bets(days_back)
    per_day_cache: dict[str, dict] = {}

    records = []
    for b in bets:
        date, mid, sel = b["date"], b["match_id"], b["sel"]
        if date not in per_day_cache:
            pf = DAILY / date / "predictions.json"
            preds = []
            if pf.exists():
                try:
                    d = json.loads(pf.read_text(encoding="utf-8"))
                    preds = d.get("predictions", d) if isinstance(d, dict) else d
                except Exception:
                    preds = []
            per_day_cache[date] = {
                "preds": {p.get("match_id"): p for p in preds},
                "closing": load_closing_index(date),
            }
        pc = per_day_cache[date]
        pred = pc["preds"].get(mid)
        taken = pred.get(SELECTION_ODDS_FIELDS[sel]) if pred else None
        kickoff_s = pred.get("kickoff") if pred else None
        if not taken or taken <= 1.0:
            records.append({**b, "status": "no_taken_odds"})
            continue
        try:
            kickoff = _parse_dt(kickoff_s) if kickoff_s else None
        except Exception:
            kickoff = None
        snaps = pc["closing"].get(_match_no_of(mid), [])
        close, gap_h = (None, float("inf"))
        if kickoff and snaps:
            close, gap_h = pick_closing_snapshot(snaps, kickoff)
        if not close or not isinstance(close.get("euro"), dict):
            records.append({**b, "status": "no_close"})
            continue
        try:
            records.append({
                **b,
                "status": "ok",
                "taken": taken,
                "clv_close": clv_vs_close(taken, float(close["euro"][["home", "draw", "away"][sel]])),
                "clv_fair": clv_vs_fair_close(taken, close["euro"], sel),
                "close_horizon_h": round(gap_h, 1),
            })
        except (KeyError, ValueError):
            records.append({**b, "status": "bad_close_data"})

    ok = [r for r in records if r["status"] == "ok"]

    def _agg(rs: list[dict]) -> dict:
        if not rs:
            return {"n": 0}
        c = [r["clv_close"] for r in rs]
        f = [r["clv_fair"] for r in rs]
        n = len(rs)
        mean_c, mean_f = statistics.mean(c), statistics.mean(f)
        sd_c = statistics.stdev(c) if n > 1 else 0.0
        sd_f = statistics.stdev(f) if n > 1 else 0.0
        t_c = mean_c / (sd_c / n ** 0.5) if sd_c > 0 else 0.0
        t_f = mean_f / (sd_f / n ** 0.5) if sd_f > 0 else 0.0
        agg = {
            "n": n,
            "clv_close_mean": round(mean_c, 4),
            "clv_close_median": round(statistics.median(c), 4),
            "clv_close_pos_pct": round(sum(1 for x in c if x > 0) / n, 3),
            "clv_close_t": round(t_c, 2),
            "clv_fair_mean": round(mean_f, 4),
            "clv_fair_median": round(statistics.median(f), 4),
            "clv_fair_pos_pct": round(sum(1 for x in f if x > 0) / n, 3),
            "clv_fair_t": round(t_f, 2),
        }
        if n >= MIN_BETS_FOR_VERDICT:
            best_t = max(agg["clv_close_t"], agg["clv_fair_t"], key=abs)
            best_mean = agg["clv_fair_mean"] if abs(agg["clv_fair_t"]) >= abs(agg["clv_close_t"]) \
                else agg["clv_close_mean"]
            if best_t >= 2.0 and best_mean > 0:
                agg["verdict"] = "POSITIVE_CLV（下注价格跑赢收盘，edge 有证据）"
            elif best_t <= -2.0 and best_mean < 0:
                agg["verdict"] = "NEGATIVE_CLV（下注价格系统性输给收盘）"
            else:
                agg["verdict"] = "TIED（无显著证据）"
        else:
            agg["verdict"] = f"样本不足（<{MIN_BETS_FOR_VERDICT}），仅记录不判定"
        return agg

    tiers = sorted({r["tier"] for r in ok})
    report = {
        "generated_at": beijing_today() + "T" + datetime.now().strftime("%H:%M:%S"),
        "window_days": days_back,
        "n_ledger_bets": len(bets),
        "coverage": {
            "ok": len(ok),
            "no_taken_odds": sum(1 for r in records if r["status"] == "no_taken_odds"),
            "no_close": sum(1 for r in records if r["status"] == "no_close"),
            "bad_close_data": sum(1 for r in records if r["status"] == "bad_close_data"),
        },
        "median_close_horizon_h": (round(statistics.median([r["close_horizon_h"] for r in ok]), 1)
                                   if ok else None),
        "overall": _agg(ok),
        "by_tier": {t: _agg([r for r in ok if r["tier"] == t]) for t in tiers},
        "records": records,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下注级 CLV 报告")
    parser.add_argument("--days", type=int, default=30, help="回看天数（默认30）")
    parser.add_argument("--quiet", action="store_true", help="只写报告不打印")
    args = parser.parse_args(argv)

    rep = build_report(args.days)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet:
        o = rep["overall"]
        print(f"下注级 CLV（近{args.days}天）: 覆盖 {rep['coverage']['ok']}/"
              f"{rep['n_ledger_bets']} 注（无收盘 {rep['coverage']['no_close']}）")
        if o.get("n"):
            print(f"  vs 原始收盘: 均值 {o['clv_close_mean']:+.2%}  中位 {o['clv_close_median']:+.2%}"
                  f"  正CLV占比 {o['clv_close_pos_pct']:.0%}  t={o['clv_close_t']}")
            print(f"  vs 去水收盘: 均值 {o['clv_fair_mean']:+.2%}  中位 {o['clv_fair_median']:+.2%}"
                  f"  正CLV占比 {o['clv_fair_pos_pct']:.0%}  t={o['clv_fair_t']}")
            print(f"  判定: {o['verdict']}")
            if rep.get("median_close_horizon_h") is not None:
                print(f"  收盘快照中位时距: {rep['median_close_horizon_h']}h")
    return 0


if __name__ == "__main__":
    sys.exit(main())
