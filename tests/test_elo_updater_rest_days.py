"""EloUpdater rest_days 行为测试。

背景：`TeamRating.rest_days` 自项目建立起从未被写入，1359 支球队恒为
默认值 3，导致 `dixon_coles.py` 里 `(home.rest_days - away.rest_days) *
cfg.rest_weight` 永远为 0 —— config 的 `rest_weight` 是死旋钮，
evolution 的 DEAD-FEATURES-REST-DAYS 探针因此长期报警。
2026-10-14 起：结算侧按 match_date 写 last_match_date/rest_days，
预测侧按 kickoff 折算但不落盘。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.learning.elo_updater import EloConfig, EloUpdater  # noqa: E402


@pytest.fixture
def updater(tmp_path: Path) -> EloUpdater:
    return EloUpdater(tmp_path / "team_ratings.json", EloConfig())


def test_first_match_keeps_default_rest(updater: EloUpdater):
    """首次结算的球队没有上一场，rest_days 保持默认 3。"""
    updater.update("曼城", "利物浦", 2, 1, match_date="2026-10-01")
    r = updater.get_rating("曼城")
    assert r.rest_days == 3
    assert r.last_match_date == "2026-10-01"


def test_rest_days_is_gap_between_matches(updater: EloUpdater):
    updater.update("曼城", "利物浦", 2, 1, match_date="2026-10-01")
    updater.update("曼城", "阿森纳", 1, 1, match_date="2026-10-05")
    r = updater.get_rating("曼城")
    assert r.rest_days == 4
    assert r.last_match_date == "2026-10-05"


def test_same_day_second_match_resets_to_zero(updater: EloUpdater):
    """同一天两场（赛程密集）→ rest_days 0，语义正确。"""
    updater.update("曼城", "利物浦", 2, 1, match_date="2026-10-05")
    updater.update("曼城", "阿森纳", 0, 2, match_date="2026-10-05")
    assert updater.get_rating("曼城").rest_days == 0


def test_out_of_order_replay_does_not_regress(updater: EloUpdater):
    """补录更早的赛果不得把 last_match_date 拉回过去，rest_days 保持旧值。"""
    updater.update("曼城", "利物浦", 2, 1, match_date="2026-10-01")
    updater.update("曼城", "阿森纳", 1, 1, match_date="2026-10-05")
    assert updater.get_rating("曼城").rest_days == 4
    updater.update("曼城", "曼联", 3, 0, match_date="2026-09-20")  # 乱序
    r = updater.get_rating("曼城")
    assert r.last_match_date == "2026-10-05"
    assert r.rest_days == 4  # gap<0 不覆盖，保留上一场算出的 4


def test_rest_gap_capped_at_14(updater: EloUpdater):
    """超长停摆不得把 rest_days 推到 60+。"""
    updater.update("利物浦", "埃弗顿", 1, 1, match_date="2026-08-01")
    updater.update("利物浦", "曼联", 2, 0, match_date="2026-10-20")
    assert updater.get_rating("利物浦").rest_days == 14


def test_prediction_side_refresh_without_persisting(updater: EloUpdater):
    """预测侧只折算、不推进 last_match_date，也不落盘。"""
    updater.update("曼城", "利物浦", 2, 1, match_date="2026-10-01")
    updater.update("曼城", "阿森纳", 1, 1, match_date="2026-10-05")
    r = updater.get_rating("曼城")
    assert updater.refresh_rest_for(r, "2026-10-08") == 3
    assert r.last_match_date == "2026-10-05"
    updater.save()
    on_disk = json.loads(updater.ratings_path.read_text())
    # 键经过 normalize_team 归一化（"曼城"→"曼彻斯特城"），不能用中文原名找
    key = next(k for k in on_disk if "曼彻斯特城" in k)
    assert on_disk[key]["last_match_date"] == "2026-10-05"


def test_prediction_side_cap_and_past_date(updater: EloUpdater):
    updater.update("曼城", "利物浦", 2, 1, match_date="2026-10-05")
    r = updater.get_rating("曼城")
    assert updater.refresh_rest_for(r, "2026-10-30") == 14  # 封顶
    assert updater.refresh_rest_for(r, "2026-10-04") == 14  # 过去日期不覆盖


def test_unknown_team_and_bad_date_tolerated(updater: EloUpdater):
    """从未结算的球队保持默认；坏日期不抛异常。"""
    r = updater.get_rating("新队")
    assert updater.refresh_rest_for(r, "2026-10-08") == 3
    assert r.last_match_date is None
    updater.update("曼城", "利物浦", 2, 1, match_date="garbage")
    assert updater.get_rating("曼城").rest_days == 3
    assert updater.get_rating("曼城").last_match_date is None


def test_no_match_date_argument_keeps_legacy_behavior(updater: EloUpdater):
    """不传 match_date 时行为与改动前完全一致（向后兼容旧调用方）。"""
    updater.update("曼城", "利物浦", 2, 1)
    r = updater.get_rating("曼城")
    assert r.rest_days == 3
    assert r.last_match_date is None


def test_roundtrip_persist_and_reload(updater: EloUpdater):
    """last_match_date 落盘后可重载，rest_days 持续演进。"""
    updater.update("曼城", "利物浦", 2, 1, match_date="2026-10-01")
    updater.update("曼城", "阿森纳", 1, 1, match_date="2026-10-05")
    updater.save()
    again = EloUpdater(updater.ratings_path, EloConfig())
    r = again.get_rating("曼城")
    assert r.last_match_date == "2026-10-05"
    assert r.rest_days == 4
    again.update("曼城", "切尔西", 0, 1, match_date="2026-10-09")
    assert again.get_rating("曼城").rest_days == 4


def test_legacy_json_without_last_match_date(updater: EloUpdater):
    """存量 team_ratings.json 没有 last_match_date 字段也能加载。"""
    legacy = {
        "巴西": {"elo": 2012.8, "attack": 1.3661, "defense": 0.6784,
                 "form": 0.4276, "injury": 0.0, "rest_days": 3},
    }
    updater.ratings_path.parent.mkdir(parents=True, exist_ok=True)
    updater.ratings_path.write_text(json.dumps(legacy))
    loaded = EloUpdater(updater.ratings_path, EloConfig())
    r = loaded.get_rating("巴西")
    assert r.rest_days == 3
    assert r.last_match_date is None
