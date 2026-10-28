"""下注级 CLV（engine/review/clv.py）单测（2026-10-11）

数学层测精确值；快照选取与 build_report 用临时目录隔离真实数据。
"""
import json
from datetime import datetime
from pathlib import Path

import engine.review.clv as clv
from engine.review.clv import (build_report, clv_vs_close, clv_vs_fair_close,
                               devig, pick_closing_snapshot, _match_no_of)


# ------------------------------------------------------ 数学层

def test_devig_sums_to_one():
    h, d, a = devig({"home": 2.0, "draw": 3.5, "away": 3.8})
    assert abs(h + d + a - 1.0) < 1e-9
    assert h > d  # 低赔 = 高概率


def test_devig_rejects_invalid_odds():
    for bad in ({"home": 1.0, "draw": 3.0, "away": 3.0},
                {"home": 0.5, "draw": 3.0, "away": 3.0}):
        try:
            devig(bad)
            assert False, "应抛 ValueError"
        except ValueError:
            pass


def test_clv_vs_close_math():
    assert abs(clv_vs_close(2.10, 2.00) - 0.05) < 1e-9   # 拿到比收盘高 5%
    assert abs(clv_vs_close(1.95, 2.00) + 0.025) < 1e-9


def test_clv_vs_fair_close_math():
    # 收盘 2.0/3.5/3.8 → 去水公平概率；taken 2.10 对主胜
    fair = devig({"home": 2.0, "draw": 3.5, "away": 3.8})[0]
    v = clv_vs_fair_close(2.10, {"home": 2.0, "draw": 3.5, "away": 3.8}, 0)
    assert abs(v - (2.10 * fair - 1)) < 1e-9


# ------------------------------------------------------ 收盘快照选取

def test_pick_closing_takes_latest_before_kickoff():
    kickoff = datetime(2026, 10, 12, 1, 0, 0)
    snaps = [
        {"ts": "2026-10-10 08:00:00", "euro": {"home": 1.10, "draw": 7.0, "away": 14.0}},
        {"ts": "2026-10-11 15:00:00", "euro": {"home": 1.15, "draw": 6.2, "away": 11.0}},
        {"ts": "2026-10-11 20:00:00", "euro": {"home": 1.18, "draw": 6.0, "away": 10.0}},
        {"ts": "2026-10-12 02:00:00", "euro": {"home": 1.20, "draw": 5.5, "away": 9.0}},  # 开赛后
    ]
    s, gap = pick_closing_snapshot(snaps, kickoff)
    assert s["ts"] == "2026-10-11 20:00:00"
    assert abs(gap - 5.0) < 1e-6


def test_pick_closing_rejects_stale_snapshots():
    kickoff = datetime(2026, 10, 12, 1, 0, 0)
    snaps = [{"ts": "2026-10-09 08:00:00", "euro": {}}]   # 距开赛 41h > 36h
    s, gap = pick_closing_snapshot(snaps, kickoff)
    assert s is None and gap == float("inf")


def test_match_no_extraction():
    assert _match_no_of("2026-10-10_周六022") == "周六022"
    assert _match_no_of("周六022") == "周六022"


# ------------------------------------------------------ build_report 集成（临时目录）

def _make_fixture(tmp_path: Path, day: str, match_id: str, match_no: str,
                  taken: float, close_seq: list[dict], sel: int = 0, tier: str = "mid",
                  preds_all: list[dict] | None = None, file_id: int = 3775639):
    d = tmp_path / "daily" / day
    d.mkdir(parents=True, exist_ok=True)
    if preds_all is not None:
        # 多场共用一份 predictions.json（真实结构），由调用方一次性写入
        (d / "predictions.json").write_text(json.dumps(preds_all), encoding="utf-8")
    else:
        odds = {0: taken, 1: 3.4, 2: 3.6}
        (d / "predictions.json").write_text(json.dumps([{
            "match_id": match_id, "kickoff": f"{day} 20:00:00",
            "home_odds": odds[0], "draw_odds": odds[1], "away_odds": odds[2],
            "confidence_tier": tier,
        }]), encoding="utf-8")
    (tmp_path / "state" / "odds_series").mkdir(parents=True, exist_ok=True)
    with open(tmp_path / "state" / "odds_series" / f"{day}_{file_id}.jsonl", "w", encoding="utf-8") as f:
        for s in close_seq:
            f.write(json.dumps({**s, "match_no": match_no}) + "\n")


def test_build_report_full_path(tmp_path, monkeypatch):
    day = "2026-10-10"
    preds_all = []
    ledger_rows = []
    for i, taken in enumerate([2.20, 1.90]):   # 第一注跑赢收盘，第二注跑输
        mid = f"{day}_周六{i:03d}"
        preds_all.append({
            "match_id": mid, "kickoff": f"{day} 20:00:00",
            "home_odds": taken, "draw_odds": 3.4, "away_odds": 3.6,
            "confidence_tier": "mid",
        })
        ledger_rows.append({"date": day, "match_id": mid, "best_selection": 0,
                            "confidence_tier": "mid", "hit": True, "pnl": 1.0})
        _make_fixture(tmp_path, day, mid, f"周六{i:03d}", taken,
                      [{"ts": f"{day} 15:00:00",
                        "euro": {"home": 2.00, "draw": 3.5, "away": 3.8}}],
                      sel=0, preds_all=preds_all, file_id=3775639 + i)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "review_ledger.jsonl").write_text(
        "\n".join(json.dumps(r) for r in ledger_rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(clv, "DAILY", tmp_path / "daily")
    monkeypatch.setattr(clv, "ODDS_SERIES", tmp_path / "state" / "odds_series")
    monkeypatch.setattr(clv, "LEDGER", tmp_path / "state" / "review_ledger.jsonl")

    rep = build_report(days_back=30)
    assert rep["coverage"]["ok"] == 2 and rep["coverage"]["no_close"] == 0
    o = rep["overall"]
    assert o["n"] == 2
    assert o["verdict"].startswith("样本不足")          # n<30 不判定
    assert o["clv_close_mean"] > 0                      # 2.20/2.00-1=+10% 与 1.90/2.00-1=-5% 均值 +2.5%
    recs = {r["match_id"]: r for r in rep["records"]}
    assert recs[f"{day}_周六000"]["clv_close"] > 0
    assert recs[f"{day}_周六001"]["clv_close"] < 0


def test_build_report_counts_missing_close(tmp_path, monkeypatch):
    day = "2026-10-10"
    _make_fixture(tmp_path, day, f"{day}_周六000", "周六000", 2.0,
                  close_seq=[])   # 无快照
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "review_ledger.jsonl").write_text(
        json.dumps({"date": day, "match_id": f"{day}_周六000",
                    "best_selection": 0, "confidence_tier": "mid"}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(clv, "DAILY", tmp_path / "daily")
    monkeypatch.setattr(clv, "ODDS_SERIES", tmp_path / "state" / "odds_series")
    monkeypatch.setattr(clv, "LEDGER", tmp_path / "state" / "review_ledger.jsonl")

    rep = build_report(days_back=30)
    assert rep["coverage"]["no_close"] == 1 and rep["overall"].get("n", 0) == 0
