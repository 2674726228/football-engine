"""挑战池队名匹配回归（2026-10-22）。

原实现只在原文精确相等时命中：模糊分支把主客写反了——
拿池子主队比预测**客队**、拿池子客队比预测**主队**，等于没写。
实测 10-22 板 11 场预测只进池 6 场，而站点板上 11 场全在。

本文件直接测 challenge_hourly.match_prediction()，不复制逻辑。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

ch = importlib.import_module("challenge_hourly")


def _pred(*pairs):
    return {(h, a): {"home_team": h, "away_team": a} for h, a in pairs}


def test_exact_match():
    pred = _pred(("格拉茨风暴", "马赛"))
    assert ch.match_prediction(pred, "格拉茨风暴", "马赛") is pred[("格拉茨风暴", "马赛")]


def test_reversed_fixture_must_not_match():
    """主客颠倒不是同一场——原实现会把两侧互相包含的队名当成命中。"""
    pred = _pred(("马赛", "格拉茨风暴"))
    assert ch.match_prediction(pred, "格拉茨风暴", "马赛") is None


def test_reversed_fixture_with_containment_must_not_match():
    pred = _pred(("塞尔塔", "凯尔特人"))
    assert ch.match_prediction(pred, "凯尔特人", "维戈塞尔塔") is None


def test_global_alias_normalized_exact():
    """engine.team_aliases 已有的简称映射要走归一化，而不是靠子串。"""
    pred = _pred(("巴黎圣日耳曼", "马赛"))
    assert ch.match_prediction(pred, "巴黎圣曼", "马赛") is not None


def test_pool_alias_cases_from_2026_10_22_board():
    cases = [
        (("萨迪纳摩", "奈梅亨"), ("萨格勒布迪纳摩", "奈梅亨")),
        (("克里特", "勒沃库森"), ("OFI克雷迪", "勒沃库森")),
        (("利勒斯特伦", "皇家社会"), ("利勒斯特罗姆", "皇家社会")),
        (("弗拉门戈", "拉普大学"), ("弗拉门戈", "拉普拉塔大学生")),
    ]
    pred = _pred(*[p for p, _ in cases])
    for pred_pair, board_pair in cases:
        assert ch.match_prediction(pred, *board_pair) is pred[pred_pair], board_pair


def test_containment_same_orientation():
    pred = _pred(("凯尔特人", "塞尔塔"))
    assert ch.match_prediction(pred, "凯尔特人", "维戈塞尔塔") is not None


def test_reserve_team_not_merged_into_first_team():
    pred = _pred(("赫罗纳", "塞尔塔"))
    assert ch.match_prediction(pred, "赫罗纳", "塞尔塔B队") is None


def test_youth_team_not_merged_into_senior_team():
    """10-21 板上同时有里尔 vs 加拉塔萨雷 和 里尔U19 vs 加拉塔萨雷U19。"""
    pred = _pred(("里尔", "加拉塔萨雷"))
    assert ch.match_prediction(pred, "里尔U19", "加拉塔萨雷U19") is None


def test_women_team_not_merged_into_men_team():
    pred = _pred(("中国", "日本"))
    assert ch.match_prediction(pred, "中国女足", "日本女足") is None


def test_unrelated_teams_no_match():
    pred = _pred(("格拉茨风暴", "马赛"))
    assert ch.match_prediction(pred, "伯恩茅斯", "AC米兰") is None


def test_both_sides_must_match():
    pred = _pred(("凯尔特人", "塞尔塔"))
    assert ch.match_prediction(pred, "凯尔特人", "勒沃库森") is None
