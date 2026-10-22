"""CI 数据兜底对账（2026-10-23）：只读仓库数据，口径与本地自动化一致。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import challenge_reconcile as cr  # noqa: E402


def _w(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _use_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "STATE", tmp_path / "state")
    monkeypatch.setattr(cr, "DAILY", tmp_path / "daily")


def test_pools_hit_rate_and_break_even(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    recs = [
        {"match": "Alpha vs Beta", "pick": "主胜@2.00", "gate": "ev_site",
         "site_odds": 2.0, "submitted_at": "2026-10-22 12:00:00"},
        {"match": "Gamma vs Delta", "pick": "客胜@3.00", "gate": "ev_site",
         "site_odds": 3.0, "submitted_at": "2026-10-22 12:00:00"},
        {"match": "Eps vs Zeta", "pick": "主胜@1.50", "gate": "ev_site",
         "site_odds": 1.5, "exp": "E10", "submitted_at": "2026-10-22 15:00:00"},
    ]
    _w(cr.STATE / "challenge_state.json", {"records": recs})
    _w(cr.DAILY / "2026-10-22" / "results_sina.json", [
        {"home_team": "Alpha", "away_team": "Beta", "home_score": 2, "away_score": 0},
        {"home_team": "Eps", "away_team": "Zeta", "home_score": 1, "away_score": 0},
    ])

    e8 = cr.pool_stats(recs, "E8")
    assert e8["n"] == 2 and e8["judged"] == 1 and e8["hits"] == 1
    assert abs(e8["hit_rate"] - 1.0) < 1e-9
    assert abs(e8["roi"] - 1.0) < 1e-9          # 2.00 命中 → +100%
    assert abs(e8["break_even"] - (0.5 + 1 / 3) / 2) < 1e-9

    e10 = cr.pool_stats(recs, "E10")
    assert e10["n"] == 1 and e10["judged"] == 1 and abs(e10["roi"] - 0.5) < 1e-9


def test_handicap_walk_is_not_a_hit(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    recs = [{"match": "Home vs Away", "pick": "客+1@1.90", "gate": "ev_site",
             "site_odds": 1.9, "submitted_at": "2026-10-22 12:00:00"}]
    _w(cr.STATE / "challenge_state.json", {"records": recs})
    _w(cr.DAILY / "2026-10-22" / "results_sina.json", [
        {"home_team": "Home", "away_team": "Away", "home_score": 1, "away_score": 0},
    ])
    s = cr.pool_stats(recs, "E8")
    assert s["judged"] == 1 and s["hits"] == 0, "走盘不算命中"


def test_snapshot_line_reports_age_and_gap(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    _w(cr.STATE / "challenge_state.json", {"records": []})
    _w(cr.STATE / "njstats" / "my_predictions" / "2026-10-21.json",
       {"snapshot_at": "2026-10-21 00:00:00",
        "records": [{"home": "Foo", "away": "Bar"}]})
    line = cr.snapshot_line()
    assert "可能过期" in line, line
    assert "仓库未记 1 条" in line and "Foo vs Bar" in line


def test_missing_files_do_not_crash(tmp_path, monkeypatch, capsys):
    _use_tmp(tmp_path, monkeypatch)
    assert cr.main([]) == 0
    out = capsys.readouterr().out
    assert "E8: n=0/60" in out and "无已入库的「已预测」快照" in out
