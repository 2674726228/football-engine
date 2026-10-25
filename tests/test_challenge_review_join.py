"""挑战记录 → 预测的队名 join（2026-10-26）。

原实现只查原文和两个手写别名，实测把已在账本结算的注永远算成挂起：
维戈塞尔塔/塞尔塔、布里斯班狮吼/布里斯班、神户胜利船/神户胜利、
利勒斯特罗姆/利勒斯特伦。现在用 sources 层 match_pair + 提交日期就近选择。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from challenge_review import resolve_record  # noqa: E402


def _p(mid, h, a):
    return {"match_id": mid, "home_team": h, "away_team": a}


def test_site_name_variants_resolve():
    rows = [
        ("2026-10-22", _p("m1", "凯尔特人", "塞尔塔")),
        ("2026-10-23", _p("m2", "布里斯班", "珀斯")),
        ("2026-10-22", _p("m3", "利勒斯特伦", "皇家社会")),
        ("2026-10-24", _p("m4", "神户胜利", "町田泽维亚")),
    ]
    cases = [
        ("凯尔特人 vs 维戈塞尔塔", "2026-10-22", "m1"),
        ("布里斯班狮吼 vs 珀斯光荣", "2026-10-23", "m2"),
        ("利勒斯特罗姆 vs 皇家社会", "2026-10-22", "m3"),
        ("神户胜利船 vs 町田泽维亚", "2026-10-24", "m4"),
    ]
    for match, day, mid in cases:
        got = resolve_record({"match": match, "submitted_at": f"{day} 12:00:00"}, rows)
        assert got and got["match_id"] == mid, (match, got)


def test_date_window_prevents_false_match():
    rows = [("2026-10-22", _p("m1", "A", "B"))]
    assert resolve_record({"match": "A vs B", "submitted_at": "2026-10-26 12:00:00"},
                          rows) is None
