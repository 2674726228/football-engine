"""E4 换盘口收益的回归测试。

重点是 `parse_score` / `parse_odds` 必须**分开**。
本会话踩过：用赔率解析器读比分时，`0 > 1.0` 为假 → 所有 0 球/1 球比赛
被静默丢弃，样本从 67,613 掉到 3,997，只剩高进球场次，
Pinnacle ROI 被算成 −29.65%（真实值 −1.32%）。

输出"看起来正常"（有注数、有 ROI、有 t），但样本已经偏到毫无意义 ——
这类静默过滤比直接报错危险得多。
"""
import pytest

pytest.importorskip("numpy")

from engine.backtest.book_vig import (  # noqa: E402
    BOOKS, devig, overround, parse_odds, parse_score,
)


class TestParsers:
    def test_score_accepts_zero_and_one(self):
        """0 球和 1 球是绝大多数比赛，解析器必须收。"""
        for v in ("0", "1", "2", "3", 0, 1, 2, 3, "0.0", "1.0"):
            assert parse_score(v) is not None, f"比分 {v!r} 被误丢弃"

    def test_score_rejects_missing_and_absurd(self):
        for v in (None, "", "NA", "abc", -1, 16, 99):
            assert parse_score(v) is None, f"比分 {v!r} 不该被接受"

    def test_odds_rejects_below_one(self):
        """赔率必须 >1.0。这是它和比分解析器的**唯一**区别。"""
        assert parse_odds("2.10") == 2.10
        for v in ("1.0", "0.9", "1", 0, None, "", "abc"):
            assert parse_odds(v) is None, f"赔率 {v!r} 不该被接受"

    def test_parsers_are_not_interchangeable(self):
        """显式锁住这条教训：0 球对赔率解析器是非法的，对比分解析器是合法的。"""
        assert parse_odds("0") is None
        assert parse_score("0") == 0
        assert parse_odds("1") is None
        assert parse_score("1") == 1

    def test_score_zero_share_is_material(self):
        """若比分 0 被丢弃，样本会严重偏向高进球场。用真实分布说明量级。"""
        # 平局的概率在真实足球里约 25%，全靠 parse_score("0") 收进来
        assert parse_score("0") == 0


class TestDevig:
    def test_devig_sums_to_one(self):
        p = devig([2.10, 3.40, 3.60])
        assert abs(sum(p) - 1.0) < 1e-12

    def test_overround_of_typical_book(self):
        # 欧洲盘典型 5~7%
        assert 1.03 < overround([2.10, 3.40, 3.60]) < 1.10

    def test_devig_inverts_odds_ordering(self):
        """赔率越低概率越高 —— 去水后必须是**逆序**，不是保序。"""
        p = devig([5.78, 4.14, 1.64])
        assert p[0] < p[1] < p[2]
        # 最低赔率的那一侧概率应过半（1.64 → ~0.57）
        assert p[2] > 0.5


class TestBookRegistry:
    def test_known_columns_exist_in_dataset(self):
        """BOOKS 里列的名字不能拼错 —— 拼错会静默返回全 None，
        在报告里表现为「样本不足」，看起来像数据问题而不是代码问题。"""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        files = sorted((root / "data" / "historical" / "football_data").rglob("*.csv"))
        assert files, "找不到 football_data"
        import csv
        cols: set[str] = set()
        for fp in files[:40]:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                cols.update(next(csv.reader(fh)))
        for name, c3 in BOOKS.items():
            missing = [c for c in c3 if c not in cols]
            assert not missing, f"{name} 的列 {missing} 在数据里不存在（拼错？）"

    def test_closing_and_opening_are_distinct_columns(self):
        """开盘与收盘必须是不同的列名。若被写成同一组，'换盘口' 会变成
        '同一个盘口算两遍'，结论直接作废。"""
        assert BOOKS["Pinnacle(PS)"] != BOOKS["PS开盘"]
        assert BOOKS["B365"] != BOOKS["B365开盘"]


# ---------------------------------------------------------------- Shin 去水

class TestDevigShin:
    """devig_shin 的二分上限修复回归（2026-10-15）。

    原 `hi = 1 - 1/max(implied)` 在 max(implied)>0.5 时为负 → z 恒负 →
    inner 变复数 → 100 轮后必走 fallback（比例归一化）。实测仓库 951 场
    竞彩数据与 1520 场 Pinnacle 欧赔 **100% 命中 fallback**，
    即 select_devig_method 从来就是乘法归一化。
    """

    @staticmethod
    def _shin(odds):
        from engine.prediction.calibration import devig_shin
        return devig_shin(list(odds))

    def test_sums_to_one_on_favourite_heavy_odds(self):
        """大热门赔率（max implied>0.5）必须能正确去水。"""
        for odds in [(1.13, 6.30, 12.0), (1.39, 4.90, 4.85), (1.10, 8.00, 20.0)]:
            p = self._shin(odds)
            # 二分 100 轮的收敛精度：sum 落在 1±1e-7
            assert abs(sum(p) - 1.0) < 1e-7, f"{odds} 和={sum(p)}"
            assert all(0 < x < 1 for x in p), f"{odds} 出现非概率值 {p}"

    def test_favourite_is_lifted_above_proportional(self):
        """Shin 的 z>0 假设：热门概率应**高于**比例归一化（内幕交易者押热门）。

        这是"Shin 真的在算"与"退化成比例法"的可区分特征——若这个断言失败，
        说明又走 fallback 了（正是 2026-10-15 之前的线上行为）。
        """
        odds = (1.13, 6.30, 12.0)
        shin = self._shin(odds)
        prop = [1 / o for o in odds]
        s = sum(prop)
        prop = [x / s for x in prop]
        assert shin[0] > prop[0] + 1e-6, (
            f"热门未被抬高：shin={shin[0]:.5f} ≤ prop={prop[0]:.5f}，"
            "说明 devig_shin 又退化成比例归一化了"
        )
        assert shin[2] < prop[2], f"冷门未被压低：shin={shin[2]:.5f} ≥ prop={prop[2]:.5f}"

    def test_ordering_is_preserved(self):
        for odds in [(2.10, 3.40, 3.80), (5.78, 4.14, 1.64), (4.00, 3.50, 1.90)]:
            p = self._shin(odds)
            order = sorted(range(3), key=lambda i: odds[i])
            for a, b in zip(order, order[1:]):
                assert p[a] > p[b], f"{odds} 去水后逆序被破坏: {p}"

    def test_no_vig_returns_implied(self):
        assert self._shin((2.0, 3.0, 6.0)) == [1 / 2, 1 / 3, 1 / 6]

    def test_matches_replay_shin_magnitude(self):
        """与 historical_replay 的固定 z=0.02 实现量级一致（交叉验证）。

        两份实现不必逐位相等（一份解 z、一份固定 z=0.02），但最大分量差
        应在 0.02 内——差太远说明其中一份算错了。
        """
        from engine.backtest.historical_replay import devig as replay_devig
        for odds in [(2.10, 3.40, 3.80), (1.39, 4.90, 4.85), (3.95, 4.25, 1.56)]:
            a = self._shin(odds)
            b = list(replay_devig(odds, "shin"))
            d = max(abs(x - y) for x, y in zip(a, b))
            # z=0.0168~0.0721 vs 固定 0.02：热门越极端差越大（实测 0.0203）
            assert d < 0.05, f"{odds} 两份 Shin 实现差 {d:.4f}，超量级"

    def test_select_devig_method_uses_real_shin(self):
        """main.py:451 的入口必须拿到真 Shin，不是比例法。"""
        from engine.prediction.calibration import select_devig_method
        odds = [1.13, 6.30, 12.0]
        p = select_devig_method(odds)
        prop = [1 / o for o in odds]
        s = sum(prop)
        prop = [x / s for x in prop]
        assert p[0] > prop[0] + 1e-6, (
            f"select_devig_method 仍是比例法（{p[0]:.5f} ≤ {prop[0]:.5f}）"
        )
