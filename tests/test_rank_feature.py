"""rank_diff 特征的接线测试（2026-10-22）。

DJYY 的 fixtures 响应里**本来就带联赛排名**，`engine/sources/djyy.py:213-214`
也把它解出来了（`fixture._rank_home` / `_rank_away`），但 `engine/main.py`
调 `build_features()` 时一个都没传 → `rank_diff` 恒为 0.0。

这和 rest_days 那次是同一个病（PROJECT.md 2026-10-14 记录）：旋钮在、
值永远是默认值、且不报错。区别只是 rest_days 已经修过一次，
这次是同类问题的第二个实例——所以修法不是再加一个 invariants 探针
（那个探针工作在球队 rating 上，而排名是 per-fixture 的），
而是把"调用点必须传"这件事用文本断言钉住，风格与本仓库对 workflow
步骤的断言一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.prediction.lgbm_model import build_features  # noqa: E402


def _f(**kw):
    return build_features(elo_home=1500.0, elo_away=1450.0, **kw)


def test_rank_diff_uses_both_ranks():
    """正数=主队排名靠前（数字小）。"""
    assert _f(rank_home=3, rank_away=8)["rank_diff"] == 5.0
    assert _f(rank_home=8, rank_away=3)["rank_diff"] == -5.0
    assert _f(rank_home=5, rank_away=5)["rank_diff"] == 0.0


def test_rank_diff_without_ranks_is_zero_not_none():
    """缺失时按本文件的既有约定填 0.0（其他特征也这样），不能塞 None 进矩阵。"""
    assert _f()["rank_diff"] == 0.0


def test_main_passes_fixture_ranks_into_features():
    """调用点必须真的传 rank_*，否则这条特征又变成死旋钮。

    2026-10-22 起 rank 先进 _lgbm_inputs 快照随预测落盘，再由快照
    传给 build_features——训练端同一份快照回放，两端同值。
    """
    src = (ROOT / "engine" / "main.py").read_text(encoding="utf-8")
    assert "\"rank_home\": getattr(fixture, \"_rank_home\", None)" in src, \
        "main.py 没把 _rank_home 写进 LGBM 输入快照"
    assert "\"rank_away\": getattr(fixture, \"_rank_away\", None)" in src, \
        "main.py 没把 _rank_away 写进 LGBM 输入快照"
    assert "rank_home=_lgbm_inputs[\"rank_home\"]" in src, \
        "main.py 没把快照里的 rank_home 传进 build_features"
    assert "rank_away=_lgbm_inputs[\"rank_away\"]" in src, \
        "main.py 没把快照里的 rank_away 传进 build_features"


def test_djyy_still_populates_rank_fields():
    """上游也得在：DJYY 若不解析 rank，上面两条就只是指向空字段。"""
    src = (ROOT / "engine" / "sources" / "djyy.py").read_text(encoding="utf-8")
    assert "_rank_home" in src and "_rank_away" in src
