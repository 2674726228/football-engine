"""DJYY / 市场 / 自有模型 的配对 Brier 检验（2026-10-28）

存在的理由：`config/prediction.json` 里 djyy 权重是 0.3，比 model(0.1) 大 3 倍，
但这个数字从没有和实测比过。账本里同一注已有三条 Brier（brier_model /
brier_market / brier_djyy），配对差检验比"各算各的均值"严格得多——
后者把不同场次的难度混在一起，会把"djyy 遇到的比赛更难"误读成"djyy 更差"。

用法：
    python scripts/djyy_market_paired_test.py                # 全量 + 近 30 天
    python scripts/djyy_market_paired_test.py --window 60
    python scripts/djyy_market_paired_test.py --json out.json

口径：
    diff = 前者 Brier − 后者 Brier；diff < 0 表示前者更好（Brier 越低越好）。
    显著性用配对 t（正态近似 p 值）+ 配对 bootstrap 的 95% CI（固定种子可复现）。
    注意：这是**事后**样本，不是 walk-forward 前瞻检验；权重变更仍须走
    `python -m engine.backtest.gate_test`，本脚本只回答"现在这一版到底谁好"。
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = ROOT / "data" / "state" / "review_ledger.jsonl"

_BOOT_ITERS = 10000
_SEED = 20261028


def load_rows(path: Path) -> list[dict]:
    """读账本，只保留三路 Brier 齐全的记录。

    账本里 8/16–9/30 那段 djyy 长期为 null（CI 侧被 403），
    这些行天然进不了样本——这是样本选择偏差，必须在报告里说清，
    不能假装"全量 N=1050"。
    """
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if all(isinstance(r.get(k), (int, float))
               for k in ("brier_model", "brier_market", "brier_djyy")):
            rows.append(r)
    return rows


def paired_stats(diffs: list[float], iters: int = _BOOT_ITERS,
                 seed: int = _SEED) -> dict:
    """配对差的均值 / t / 双侧 p / bootstrap 95% CI。"""
    n = len(diffs)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "se": None, "t": None,
                "p": None, "ci95": [None, None]}
    mean = statistics.fmean(diffs)
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else 0.0
    t = mean / se if se else 0.0
    # 双侧 p（正态近似；n>=30 时与 t 分布差别可忽略，这里不引 scipy）
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    rnd = random.Random(seed)
    boots = sorted(
        statistics.fmean([diffs[rnd.randrange(n)] for _ in range(n)])
        for _ in range(iters)
    )
    lo = boots[int(0.025 * iters)]
    hi = boots[max(0, int(0.975 * iters) - 1)]
    return {"n": n, "mean": mean, "sd": sd, "se": se, "t": t, "p": p,
            "ci95": [lo, hi]}


def verdict(stats: dict) -> str:
    """把 CI 翻译成人话。diff = 前者 − 后者。"""
    lo, hi = stats.get("ci95") or [None, None]
    if lo is None:
        return "无样本"
    if hi < 0:
        return "显著更好"
    if lo > 0:
        return "显著更差"
    return "无显著差异"


def _fmt_pp(x) -> str:
    return "—" if x is None else f"{x * 100:+.2f}pp"


def compare(rows: list[dict], label: str) -> list[dict]:
    pairs = [
        ("djyy − market", "brier_djyy", "brier_market"),
        ("djyy − model", "brier_djyy", "brier_model"),
        ("model − market", "brier_model", "brier_market"),
    ]
    if any(isinstance(r.get("brier_fused"), (int, float)) for r in rows):
        pairs.append(("fused − market", "brier_fused", "brier_market"))
    out = []
    for name, a, b in pairs:
        diffs = [r[a] - r[b] for r in rows
                 if isinstance(r.get(a), (int, float)) and isinstance(r.get(b), (int, float))]
        s = paired_stats(diffs)
        s.update({"label": label, "pair": name,
                  "mean_a": statistics.fmean([r[a] for r in rows if isinstance(r.get(a), (int, float))])
                  if diffs else None,
                  "mean_b": statistics.fmean([r[b] for r in rows if isinstance(r.get(b), (int, float))])
                  if diffs else None})
        s["verdict"] = verdict(s)
        out.append(s)
    return out


def _window(rows: list[dict], days: int) -> list[dict]:
    """取最近 N 天的子集（按账本里最大日期回推，避免依赖系统时区）。"""
    from datetime import date, timedelta

    dated = [r for r in rows if (r.get("date") or r.get("match_date"))]
    if not dated:
        return rows
    latest = max((r.get("date") or r.get("match_date"))[:10] for r in dated)
    try:
        y, m, d = (int(x) for x in latest.split("-"))
    except ValueError:
        return rows
    cut = (date(y, m, d) - timedelta(days=days)).isoformat()
    return [r for r in rows if (r.get("date") or r.get("match_date"))[:10] >= cut]


def render(results: list[dict], meta: dict) -> str:
    lines = []
    lines.append("DJYY 配对 Brier 检验")
    lines.append(f"  账本: {meta['ledger']}")
    lines.append(f"  样本: 三路 Brier 齐全 {meta['n']} 行 / 账本总行 {meta['n_total']}"
                 f"（占 {meta['coverage']:.1f}%）")
    if meta["coverage"] < 80:
        lines.append("  ⚠ 样本覆盖不足 80%：8/16–9/30 djyy 长期为 null（CI 侧 403），"
                     "此结果只代表「djyy 有数据的那批比赛」，不等于全赛季")
    lines.append("")
    header = f"  {'窗口':<10}{'对阵':<18}{'n':>5}{'均值差':>10}{'95%CI':>22}{'p':>8}  判定"
    lines.append(header)
    lines.append("  " + "-" * (len("  " + header) - 2))
    for r in results:
        if not r["n"]:
            continue
        ci = f"[{_fmt_pp(r['ci95'][0])}, {_fmt_pp(r['ci95'][1])}]"
        lines.append(f"  {r['label']:<10}{r['pair']:<18}{r['n']:>5}"
                     f"{_fmt_pp(r['mean']):>10}{ci:>22}{r['p']:>8.3f}  {r['verdict']}")
    lines.append("")
    lines.append("  注：diff = 前者 − 后者，负值表示前者 Brier 更低（更好）。")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="DJYY / 市场 / 模型 配对 Brier 检验")
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--window", type=int, default=30, help="近 N 天窗口（默认 30）")
    ap.add_argument("--json", type=Path, default=None, help="把结果写成 JSON")
    args = ap.parse_args()

    if not args.ledger.exists():
        print(f"账本不存在: {args.ledger}")
        return 2

    all_rows_raw = [ln for ln in args.ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    rows = load_rows(args.ledger)
    if not rows:
        print("账本里没有三路 Brier 齐全的记录——先跑一段时间再回来看")
        return 1

    recent = _window(rows, args.window)
    results = compare(rows, "全量") + compare(recent, f"近{args.window}天")
    meta = {
        "ledger": str(args.ledger),
        "n": len(rows),
        "n_total": len(all_rows_raw),
        "coverage": 100.0 * len(rows) / max(1, len(all_rows_raw)),
    }
    print(render(results, meta))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"meta": meta, "results": results},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  → {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
