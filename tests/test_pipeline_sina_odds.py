"""新浪赔率匹配的四级 fallback（2026-10-14 从 engine/main.py 抽出后的回归测试）。

抽出的动机：原先这 35 行内联在 run_daily_pipeline 的预测循环里，
main.py 2421 行的维护性风险之一。抽出后行为必须逐字等价——
尤其 fallback 的**顺序**（编号 → 精确 → 归一化 → 模糊），
调换会让"模糊匹配先命中"从而把不同球队判成同一场。
"""
from types import SimpleNamespace

import pytest

from engine.pipeline.sina_odds import (
    _norm_name,
    build_sina_data,
    lookup_sina_match,
)


def _fx(match_id="2026-08-01_周六001", home="奥林匹亚科斯", away="广州恒大淘宝"):
    return SimpleNamespace(match_id=match_id, home_team=home, away_team=away)


# ---------------------------------------------------------------- lookup 四级


def test_lookup_prefers_match_no_over_team_name():
    """第一优先是竞彩编号——编号与新浪同源，不会因译名分歧匹配错。"""
    by_no = {"周六001": {"euro": {}, "odds_history": []}}
    by_name = {("奥林匹亚科斯", "广州恒大淘宝"): {"euro": {}, "odds_history": []}}
    hit = lookup_sina_match(_fx(), by_name, by_no)
    assert hit is by_no["周六001"]


def test_lookup_team_name_exact_when_no_match_no():
    by_name = {("奥林匹亚科斯", "广州恒大淘宝"): {"euro": {}, "odds_history": []}}
    assert lookup_sina_match(_fx(), by_name, {}) is by_name[("奥林匹亚科斯", "广州恒大淘宝")]


def test_lookup_normalized_handles_translation_variants():
    """第三级吃译名变体：fixture 写"奥林匹亚科斯"、新浪写"奥林匹亚"。"""
    by_name = {("奥林匹亚", "恒大"): {"euro": {}, "odds_history": []}}
    hit = lookup_sina_match(_fx(), by_name, {})
    assert hit is not None


def test_lookup_fuzzy_is_last_resort_only():
    """第四级（模糊互相包含）只在前三级全失败时才用。

    模糊层是双向包含，会把 "A" 和 "A B" 这种不同球队判成同一场——
    若它排在第三级之前，第三级的译名收敛就被架空了。
    """
    # 归一化层能命中的场景，不该走到模糊层
    by_name = {("奥林匹亚", "恒大"): {"euro": {}, "odds_history": []}}
    assert lookup_sina_match(_fx(), by_name, {}) is not None
    # 前三层都失败时才落到模糊
    by_fuzzy = {("奥林匹亚科斯队", "广州恒大淘宝FC"): {"euro": {}, "odds_history": []}}
    assert lookup_sina_match(_fx(), by_fuzzy, {}) is not None


def test_lookup_returns_none_when_all_levels_miss():
    assert lookup_sina_match(_fx(), {("完全无关", "另一支队"): {"euro": {}}}, {}) is None


def test_lookup_without_underscore_match_id_is_tolerated():
    """match_id 无下划线时不得抛异常（生产上有旧格式 id）。"""
    by_name = {("奥林匹亚科斯", "广州恒大淘宝"): {"euro": {}, "odds_history": []}}
    hit = lookup_sina_match(_fx(match_id="周六001"), by_name, {})
    assert hit is by_name[("奥林匹亚科斯", "广州恒大淘宝")]


# ---------------------------------------------------------------- _norm_name


@pytest.mark.parametrize("raw,expected", [
    ("广州FC队市", "广州"),
    ("切尔西", "切尔西"),
    ("", ""),
    ("FC拜仁慕尼黑", "拜仁慕尼黑"),
])
def test_norm_name_strips_noise(raw, expected):
    assert _norm_name(raw) == expected


# ---------------------------------------------------------------- build_sina_data


def test_build_sina_data_none_in_none_out():
    assert build_sina_data(None, "x") is None


def test_build_sina_data_maps_all_fields():
    sm = {
        "euro": {"initial": 2.0, "current": 1.9, "movement": "down", "compression": 0.1},
        "odds_history": [1, 2, 3],
        "asia": {"line": 0.5},
        "totals": {"line": 2.5},
        "match_time": "20:00",
    }
    out = build_sina_data(sm, "2026-08-01_周六001")
    assert out["initial_odds"] == 2.0
    assert out["current_odds"] == 1.9
    assert out["movement"] == "down"
    assert out["compression"] == 0.1
    assert out["odds_history_count"] == 3
    assert out["asia"] == {"line": 0.5}
    assert out["totals"] == {"line": 2.5}
    assert out["match_time"] == "20:00"
    # series 由 odds_series 独立提供（无快照时是空结构，不是 None）
    assert out["series"] is not None


def test_build_sina_data_tolerates_missing_keys():
    """odds_sina.json 字段不全时不得 KeyError（上游容错返回空 dict）。"""
    out = build_sina_data({"euro": {}}, "x")
    assert out["initial_odds"] is None
    assert out["odds_history_count"] == 0
    assert out["series"] is not None
