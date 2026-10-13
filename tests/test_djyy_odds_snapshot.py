"""DJYY 赔率快照口径回归测试（2026-10-13 修正）。

## 修的是什么

`fetch_odds_snapshot` 原实现取 `bookmaker["1x2"]`（**去水概率**）再 `1/p`
转回赔率，产出的是"公平赔率"，却被当成含抽水的真实赔率存进
`OddsSnapshot`。实测同一场（19713912）：

    markets 里 Pinnacle 真实赔率:  2.43 / 3.72 / 2.82
    原实现 1/p 得到的:            2.515 / 3.85 / 2.919   ← 偏高 3~4%

偏高的量正好是抽水。拿它算 EV 会**系统性偏乐观**。

## 为什么这条重要

`sporttery.fetch_odds_snapshot` 用的是真实赔率（`f.home_odds`）。
两个 source 实现同一个 `DataSource` 接口却口径不同——
这正是项目反复警告的失真来源（clv.py 的注释专门写过）。
"""
from __future__ import annotations

from datetime import date


def _src():
    from engine.sources.djyy import DJYYSource
    return DJYYSource()


def _cmp_with(raw_odds, key="1x2_fulltime", bm_name="Pinnacle",
               bookmaker_1x2=None):
    return {
        "bookmaker": {"1x2": bookmaker_1x2 or {}, "name": bm_name},
        "markets": [{
            "key": key,
            "bookmaker": {"name": bm_name, "raw_odds": raw_odds},
        }],
        "model": {},
    }


class _Snap:
    def __init__(self, match_id, ts, ho, do, ao, source):
        self.match_id, self.timestamp = match_id, ts
        self.home_odds, self.draw_odds, self.away_odds = ho, do, ao
        self.source = source


def test_uses_real_odds_not_fair_odds(monkeypatch):
    """必须用 markets 的真实赔率，不能用 1/p 转出来的公平赔率。"""
    src = _src()

    class _F:
        match_id = "m1"
        _djyy_id = 19713912
        _has_odds = True

    monkeypatch.setattr(src, "fetch_fixtures", lambda d: [_F()])
    monkeypatch.setattr(src, "fetch_match_comparison",
                        lambda i: _cmp_with({"home": 2.82, "draw": 3.72, "away": 2.43},
                                            bookmaker_1x2={"home": 0.3426, "draw": 0.2597, "away": 0.3976}))
    monkeypatch.setattr("time.sleep", lambda s: None)
    snaps = src.fetch_odds_snapshot(date(2026, 10, 13))
    assert len(snaps) == 1
    s = snaps[0]
    # 真实赔率，不是 1/p 得到的偏高值
    assert s.home_odds == 2.82, f"拿到 {s.home_odds}（1/p 会得到 2.919）"
    assert s.away_odds == 2.43, f"拿到 {s.away_odds}（1/p 会得到 2.515）"


def test_overround_present_in_real_odds(monkeypatch):
    """真实赔率的隐含概率和必须 >1（含抽水）——这是与公平赔率的区别。"""
    src = _src()

    class _F:
        match_id = "m1"
        _djyy_id = 1
        _has_odds = True

    monkeypatch.setattr(src, "fetch_fixtures", lambda d: [_F()])
    monkeypatch.setattr(src, "fetch_match_comparison",
                        lambda i: _cmp_with({"home": 2.82, "draw": 3.72, "away": 2.43}))
    monkeypatch.setattr("time.sleep", lambda s: None)
    s = src.fetch_odds_snapshot(date(2026, 10, 13))[0]
    overround = 1 / s.home_odds + 1 / s.draw_odds + 1 / s.away_odds
    assert overround > 1.0, f"隐含概率和 {overround:.4f} <= 1，说明是去水后的公平赔率"


def test_source_tag_records_bookmaker(monkeypatch):
    """source 必须带庄名——不同庄的赔率不可比。"""
    src = _src()

    class _F:
        match_id = "m1"
        _djyy_id = 1
        _has_odds = True

    monkeypatch.setattr(src, "fetch_fixtures", lambda d: [_F()])
    monkeypatch.setattr(src, "fetch_match_comparison",
                        lambda i: _cmp_with({"home": 1.9, "draw": 3.4, "away": 3.6},
                                            bm_name="Pinnacle"))
    monkeypatch.setattr("time.sleep", lambda s: None)
    s = src.fetch_odds_snapshot(date(2026, 10, 13))[0]
    assert "pinnacle" in s.source.lower(), f"source 未记录庄名: {s.source}"


def test_skips_when_no_1x2_market(monkeypatch):
    """没有 1x2_fulltime 市场时跳过，不产生脏数据。"""
    src = _src()

    class _F:
        match_id = "m1"
        _djyy_id = 1
        _has_odds = True

    monkeypatch.setattr(src, "fetch_fixtures", lambda d: [_F()])
    monkeypatch.setattr(src, "fetch_match_comparison",
                        lambda i: {"markets": [{"key": "totals_main",
                                               "bookmaker": {"raw_odds": {"over": 1.9, "under": 1.9}}}],
                                   "bookmaker": {}, "model": {}})
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert src.fetch_odds_snapshot(date(2026, 10, 13)) == []


def test_skips_invalid_odds(monkeypatch):
    """赔率 <=1 或非数字视为非法，不能算出 0 或 NaN。"""
    src = _src()

    class _F:
        match_id = "m1"
        _djyy_id = 1
        _has_odds = True

    monkeypatch.setattr(src, "fetch_fixtures", lambda d: [_F()])
    for bad in ({"home": 1.0, "draw": 3.4, "away": 3.6},
                {"home": 0, "draw": 3.4, "away": 3.6},
                {"home": "x", "draw": 3.4, "away": 3.6}):
        monkeypatch.setattr(src, "fetch_match_comparison", lambda i, b=bad: _cmp_with(b))
        assert src.fetch_odds_snapshot(date(2026, 10, 13)) == []
