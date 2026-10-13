"""同赔分析模块回归测试（2026-10-13）。

## 修的真 bug：`_load()` 按不存在的列名读，永远加载 0 条

`data/historical/odds.csv` 的真实列名是
`date, competition, home_team, away_team, home_score, away_score, ...,
home_odds, draw_odds, away_odds`，而原实现读 `match_id` / `result` / `league`
——**三个都不存在**，于是 `result` 恒为空 → `result in ("H","D","A")` 恒 False
→ **加载 0 条**。

后果链：`records` 为空 → `analyze()` 恒 matched_count=0 →
`find_value_signals()` 恒返回空列表。**不报错，只是永远没信号**——
这也是它一直没被发现的原因。修复后加载 29586 条。

## 关于容差（不作断言，只记录实测）

默认 `tolerance=0.015`（三路赔率同时匹配）。用真实预测数据（92 场）实测：

    tol=0.015 → 14% 场次可出结论（平均样本 7）
    tol=0.1   → 91%（平均样本 87）
    tol=0.2   → 96%（平均样本 146）

即默认是"保守但可用"而非不可用。是否放宽属于策略决策，涉及样本量与
精度的权衡，**放模型侧决定**，不在采集层改默认（改了会影响所有历史回放）。
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.sources.same_odds import SameOddsAnalyzer, SameOddsConfig  # noqa: E402

CSV_PATH = ROOT / "data" / "historical" / "odds.csv"


def _mk_csv(tmp_path, rows):
    p = tmp_path / "odds.csv"
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "competition", "home_team", "away_team",
                    "home_score", "away_score", "handicap",
                    "home_odds", "draw_odds", "away_odds"])
        for r in rows:
            w.writerow(r)
    return p


def test_loads_real_historical_odds():
    """核心回归：真实 odds.csv 必须能加载出记录（修复前是 0 条）。"""
    if not CSV_PATH.exists():
        pytest.skip("无 data/historical/odds.csv")
    a = SameOddsAnalyzer(CSV_PATH)
    assert len(a.records) > 1000, f"只加载 {len(a.records)} 条——列名解析又坏了"


def test_result_derived_from_scores(tmp_path):
    """赛果由 home_score/away_score 推导（CSV 里没有 result 列）。"""
    p = _mk_csv(tmp_path, [
        ("2020-01-01", "L1", "A", "B", 2, 1, 0, 2.0, 3.4, 3.6),   # H
        ("2020-01-02", "L1", "C", "D", 1, 1, 0, 2.0, 3.4, 3.6),   # D
        ("2020-01-03", "L1", "E", "F", 0, 1, 0, 2.0, 3.4, 3.6),   # A
    ])
    a = SameOddsAnalyzer(p)
    assert len(a.records) == 3
    assert [r.result for r in a.records] == ["H", "D", "A"], "赛果推导错误"


def test_league_taken_from_competition_column(tmp_path):
    """联赛取自 competition 列（原实现读 league，不存在）。"""
    p = _mk_csv(tmp_path, [
        ("2020-01-01", "PREMIER_LEAGUE", "A", "B", 1, 0, 0, 2.0, 3.4, 3.6),
    ])
    a = SameOddsAnalyzer(p)
    assert a.records[0].league == "PREMIER_LEAGUE"


def test_match_id_falls_back_to_composite(tmp_path):
    """没有 match_id 列时用 日期_主队_vs_客队 合成（保证可追溯）。"""
    p = _mk_csv(tmp_path, [
        ("2020-01-01", "L1", "Alpha", "Beta", 1, 0, 0, 2.0, 3.4, 3.6),
    ])
    a = SameOddsAnalyzer(p)
    assert "2020-01-01" in a.records[0].match_id
    assert "Alpha" in a.records[0].match_id


def test_analyze_finds_same_odds(tmp_path):
    """同赔匹配能找到记录（修复前恒为 0）。"""
    rows = [("2020-01-01", "L1", "A", "B", 1, 0, 0, 2.0, 3.4, 3.6) for _ in range(6)]
    p = _mk_csv(tmp_path, rows)
    a = SameOddsAnalyzer(p)
    r = a.analyze(2.0, 3.4, 3.6)
    assert r.matched_count == 6


def test_below_min_matches_returns_empty_result(tmp_path):
    """样本不足时返回空结果（不编造结论）。"""
    rows = [("2020-01-01", "L1", "A", "B", 1, 0, 0, 2.0, 3.4, 3.6) for _ in range(3)]
    p = _mk_csv(tmp_path, rows)
    a = SameOddsAnalyzer(p)
    r = a.analyze(2.0, 3.4, 3.6)
    assert r.matched_count == 3
    assert r.home_win_rate == 0.0, "样本不足时不应给出胜率"


def test_tolerance_is_configurable(tmp_path):
    """容差可配置——放宽属于策略决策，调用方应能调。"""
    rows = [("2020-01-01", "L1", "A", "B", 1, 0, 0, 2.0, 3.4, 3.6) for _ in range(6)]
    p = _mk_csv(tmp_path, rows)
    # 严格容差：2.5 匹配不到 2.0
    a_strict = SameOddsAnalyzer(p, SameOddsConfig(tolerance=0.015))
    assert a_strict.analyze(2.5, 3.4, 3.6).matched_count == 0
    # 宽松容差：能匹配到
    a_loose = SameOddsAnalyzer(p, SameOddsConfig(tolerance=0.6))
    assert a_loose.analyze(2.5, 3.4, 3.6).matched_count == 6


def test_missing_file_is_silent_not_crash(tmp_path):
    """文件不存在时静默返回空（不抛异常，避免毁掉整条管线）。"""
    a = SameOddsAnalyzer(tmp_path / "nope.csv")
    assert a.records == []


def test_malformed_rows_skipped(tmp_path):
    """坏行跳过，不能让一条脏数据毁掉整个库。"""
    p = _mk_csv(tmp_path, [
        ("2020-01-01", "L1", "A", "B", 1, 0, 0, 2.0, 3.4, 3.6),
        ("bad-row"),
        ("2020-01-02", "L1", "C", "D", "", "", "", "", "", ""),
    ])
    a = SameOddsAnalyzer(p)
    assert len(a.records) == 1, "坏行未被正确跳过"
