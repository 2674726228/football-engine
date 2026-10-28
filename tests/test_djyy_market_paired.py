"""配对 Brier 检验的统计口径（2026-10-28）

为什么要有这个脚本：`config/prediction.json` 给 djyy 0.3 权重，而该数字从未
与实测对照过。账本已逐注落 brier_model / brier_market / brier_djyy，用**配对差**
比"各算均值"严格——后者把不同场次难度混在一起。

这里锁三件事：
1. 均值 / 配对 t / CI 的计算口径（diff = 前者 − 后者，负值=前者更好）。
2. CI 判定：全负 → 显著更好；全正 → 显著更差；跨 0 → 无显著差异。
3. 账本缺字段的行必须被排除，不能当 0 参与平均。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import djyy_market_paired_test as dpt


def _row(date: str, model: float, market: float, djyy: float) -> dict:
    return {"date": date, "brier_model": model, "brier_market": market,
            "brier_djyy": djyy}


def test_paired_stats_matches_manual_mean():
    diffs = [0.01, -0.02, 0.03, -0.01, 0.0]
    s = dpt.paired_stats(diffs, iters=2000)
    assert s["n"] == 5
    assert abs(s["mean"] - 0.002) < 1e-12
    lo, hi = s["ci95"]
    assert lo <= s["mean"] <= hi


def test_verdict_bands():
    assert dpt.verdict({"ci95": [-0.03, -0.005]}) == "显著更好"
    assert dpt.verdict({"ci95": [0.005, 0.03]}) == "显著更差"
    assert dpt.verdict({"ci95": [-0.01, 0.02]}) == "无显著差异"
    assert dpt.verdict({"ci95": [None, None]}) == "无样本"


def test_constant_negative_diff_is_significant():
    """全部同号的差，SE 为 0，必须落进"显著"而不是被判无差异。"""
    s = dpt.paired_stats([-0.02] * 40, iters=500)
    assert s["ci95"] == [-0.02, -0.02]
    assert dpt.verdict(s) == "显著更好"


def test_load_rows_skips_incomplete(tmp_path):
    p = tmp_path / "ledger.jsonl"
    rows = [
        _row("2026-10-22", 0.60, 0.57, 0.58),
        {"date": "2026-10-22", "brier_model": 0.6},          # 缺 market/djyy
        {"date": "2026-10-23", "brier_market": None,
         "brier_model": 0.6, "brier_djyy": 0.6},              # 显式 null
        _row("2026-10-23", 0.55, 0.54, 0.56),
    ]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    assert len(dpt.load_rows(p)) == 2


def test_window_uses_latest_date_in_ledger(tmp_path):
    rows = [
        _row("2026-09-01", 0.6, 0.57, 0.58),
        _row("2026-10-20", 0.6, 0.57, 0.58),
        _row("2026-10-28", 0.6, 0.57, 0.58),
    ]
    recent = dpt._window(rows, 30)
    assert [r["date"] for r in recent] == ["2026-10-20", "2026-10-28"]


def test_compare_emits_one_row_per_pair():
    rows = [_row("2026-10-28", 0.60, 0.57, 0.58) for _ in range(3)]
    out = dpt.compare(rows, "全量")
    pairs = [r["pair"] for r in out]
    assert "djyy − market" in pairs and "djyy − model" in pairs
    assert all(r["n"] == 3 for r in out)
    # fused 字段缺失时不该凭空多出一行
    assert "fused − market" not in pairs
