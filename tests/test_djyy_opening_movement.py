"""Pinnacle 初盘→即时盘移动的提取（2026-10-13 新接入）。

## 为什么补这个

`DJYYSource.get_opening_vs_current()` 早就写好，但**从未被调用过**
（grep 全仓库只有定义处）。而它提供的正是项目缺的拼图：

STATUS.md 记的缺口：「开盘列（PSH/PSD/PSA）样本为 0，带开盘列的旧格式
文件又没有比分，要做开收盘对比必须先合并两套 schema」。
odds_series 里我们自己的快照只有竞彩价，没有锐线开盘。
DJYY 的 comparison **零额外请求**就带 Pinnacle 开盘价（实测可用性 44%）。

## 锁什么

1. 只在 `1x2_fulltime` 市场取（别的市场赔率不可比）
2. 必须带 bookmaker 名（不同庄的移动混起来就没意义）
3. `prob_shift` 是**去水后**的概率位移——赔率是非线性的，
   `2.0→2.1` 与 `10.0→10.1` 含义天差地别，直接减赔率会误导
4. 数据缺失时返回 None（不抛异常，不能因为一个可选字段毁掉整场富化）
"""

from __future__ import annotations

from engine.sources.manager import _opening_movement


def _cmp(opening, current, key="1x2_fulltime", bm_name="Pinnacle"):
    return {
        "markets": [{
            "key": key,
            "bookmaker": {
                "name": bm_name,
                "opening_raw_odds": opening,
                "raw_odds": current,
            },
        }]
    }


def test_extracts_movement_and_prob_shift():
    r = _opening_movement(_cmp(
        {"home": 1.28, "draw": 5.0, "away": 10.0},
        {"home": 1.26, "draw": 6.47, "away": 10.74},
    ))["1x2_fulltime"]
    assert r is not None
    assert r["bookmaker"] == "Pinnacle"
    assert r["movement"]["home"] == -0.02
    assert r["movement"]["draw"] == 1.47
    # 概率位移：主胜隐含概率上升（赔率下降），平局下降
    assert r["prob_shift"]["home"] > 0
    assert r["prob_shift"]["draw"] < 0
    # 概率位移三项和必须为 0（归一化后）
    assert abs(sum(r["prob_shift"].values())) < 1e-6


def test_prob_shift_sums_to_zero_across_scales():
    """概率位移必须在任何赔率量级下都归一化（这是它比赔率差强的地方）。

    赔率差在 2.0→2.1（+0.1）与 10.0→10.1（+0.1）看起来一样，
    但隐含概率变化差一个数量级。prob_shift 消除这个非线性。
    """
    r1 = _opening_movement(_cmp({"home": 2.0, "draw": 3.4, "away": 3.6},
                                {"home": 2.1, "draw": 3.3, "away": 3.5}))["1x2_fulltime"]
    r2 = _opening_movement(_cmp({"home": 10.0, "draw": 5.0, "away": 1.3},
                                {"home": 10.1, "draw": 4.9, "away": 1.29}))["1x2_fulltime"]
    assert abs(sum(r1["prob_shift"].values())) < 1e-6
    assert abs(sum(r2["prob_shift"].values())) < 1e-6
    # 相同的赔率差，概率位移量级不同
    assert abs(r1["movement"]["home"] - r2["movement"]["home"]) < 1e-9
    assert abs(r1["prob_shift"]["home"]) != abs(r2["prob_shift"]["home"])


def test_collects_all_markets_not_just_1x2():
    """必须收全部市场，不只看 1x2（2026-10-13 实测：10 个市场里 8 个带开盘价）。

    第一版只看 `1x2_fulltime`，漏掉了 `totals_main`（开盘价 9/9 可用，
    是本批里覆盖率最高的）等 7 个市场。
    """
    r = _opening_movement({"markets": [
        {"key": "1x2_fulltime", "bookmaker": {"name": "Pinnacle",
         "opening_raw_odds": {"home": 1.9, "draw": 3.4, "away": 3.6},
         "raw_odds": {"home": 1.8, "draw": 3.5, "away": 3.7}}},
        {"key": "totals_main", "bookmaker": {"name": "Pinnacle",
         "opening_raw_odds": {"over": 1.82, "under": 2.02},
         "raw_odds": {"over": 1.95, "under": 1.88}}},
    ]})
    assert r is not None
    assert set(r) == {"1x2_fulltime", "totals_main"}, "非 1x2 市场被丢弃了"
    assert r["totals_main"]["bookmaker"] == "Pinnacle"
    assert r["totals_main"]["legs"] == 2


def test_market_without_opening_is_skipped():
    """只有即时价、没有开盘价的市场要跳过（无法算变动）。"""
    r = _opening_movement({"markets": [
        {"key": "corners_main", "bookmaker": {"name": "Pinnacle",
         "opening_raw_odds": None, "raw_odds": {"over": 1.84, "under": 1.97}}},
    ]})
    assert r is None


def test_prob_shift_only_for_small_markets():
    """prob_shift 只对腿数少的市场算（correct_score 40+ 腿算出来没意义）。"""
    big = {f"{i}-0": 10.0 + i for i in range(1, 9)}
    big_cur = {f"{i}-0": 11.0 + i for i in range(1, 9)}
    r = _opening_movement({"markets": [
        {"key": "correct_score", "bookmaker": {"name": "bet365",
         "opening_raw_odds": big, "raw_odds": big_cur}},
        {"key": "1x2_fulltime", "bookmaker": {"name": "Pinnacle",
         "opening_raw_odds": {"home": 1.9, "draw": 3.4, "away": 3.6},
         "raw_odds": {"home": 1.8, "draw": 3.5, "away": 3.7}}},
    ]})
    assert r["correct_score"]["prob_shift"] is None, "40 腿市场不该算 prob_shift"
    assert r["1x2_fulltime"]["prob_shift"] is not None


def test_requires_bookmaker_name():
    """必须带 bookmaker 名——不同庄的移动混起来会让特征失去意义。"""
    r = _opening_movement(_cmp({"home": 1.9, "draw": 3.4, "away": 3.6},
                               {"home": 1.8, "draw": 3.5, "away": 3.7},
                               bm_name=""))["1x2_fulltime"]
    assert r["bookmaker"] == "", "上游没给庄名时留空，不要瞎猜"


def test_missing_data_returns_none_not_raises():
    """数据缺失返回 None——可选字段不能毁掉整场富化。"""
    assert _opening_movement(None) is None
    assert _opening_movement({}) is None
    assert _opening_movement({"markets": []}) is None
    assert _opening_movement({"markets": [{"key": "1x2_fulltime"}]}) is None
    # opening 或 current 缺一
    assert _opening_movement(_cmp({}, {"home": 1.9, "draw": 3.4, "away": 3.6})) is None
    assert _opening_movement(_cmp({"home": 1.9, "draw": 3.4, "away": 3.6}, {})) is None


def test_non_numeric_leg_is_dropped_not_whole_market():
    """单腿非法时丢那一腿，不丢整个市场（别的腿的数据仍有价值）。

    但要保证：算出来的 movement/prob_shift 里**不含 NaN**。
    """
    r = _opening_movement(_cmp({"home": "x", "draw": 3.4, "away": 3.6},
                               {"home": 1.8, "draw": 3.5, "away": 3.7}))
    assert r is not None
    m = r["1x2_fulltime"]
    assert "home" not in m["opening"], "非数字腿应被剔除"
    assert m["legs"] == 2
    # 剩余腿的数值必须是干净的
    for v in m["movement"].values():
        assert v == v, "movement 里出现 NaN"


def test_all_legs_invalid_drops_market():
    """所有腿都非法时该市场整体丢弃。"""
    r = _opening_movement(_cmp({"home": "x", "draw": "y", "away": "z"},
                               {"home": 1.8, "draw": 3.5, "away": 3.7}))
    assert r is None


def test_odds_at_or_below_one_rejected():
    """赔率 <=1 非法（不是有效赔率），该腿剔除。"""
    r = _opening_movement(_cmp({"home": 1.0, "draw": 3.4, "away": 3.6},
                               {"home": 1.8, "draw": 3.5, "away": 3.7}))
    assert r is not None
    assert "home" not in r["1x2_fulltime"]["opening"]
