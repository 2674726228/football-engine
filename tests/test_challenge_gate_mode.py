"""挑战闸门量纲的回归测试（2026-10-22）。

`edge`（模型−市场）与 `ev_site`（P×站点结算价−1）是两种不同的量纲。
2026-10-22 实测：7 场未开赛对照里 edge 全为负（最大 −0.23pp），
现行 1.5pp 档不可达；同一批换成站点价 EV 有 5/7 ≥ 0。
所以闸门口径要能切换——但**默认必须是 edge**，切换属策略层改动，
由 docs/EXPERIMENTS.md 的 E8 前瞻实验裁决。

本文件直接驱动 challenge_hourly.main()（教训见 test_challenge_server_dedupe.py：
复刻逻辑来测=在测自己写的副本）。
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

ch = importlib.import_module("challenge_hourly")


def _setup(tmp_path, monkeypatch, matches, policy=None, site_odds=True):
    state = tmp_path / "challenge_state.json"
    monkeypatch.setattr(ch, "STATE", state)
    pol = tmp_path / "challenge_policy.json"
    if policy is not None:
        pol.write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setattr(ch, "POLICY", pol)
    monkeypatch.setenv("NJSTATS_JWT", "test-jwt")

    day = dt.datetime.now(ch.BJT).strftime("%Y-%m-%d")
    d = tmp_path / "data" / "daily" / day
    d.mkdir(parents=True, exist_ok=True)
    preds = [{"home_team": m["home"], "away_team": m["away"],
              "home_win_prob": m["p"], "draw_prob": 0.2,
              "away_win_prob": 1 - m["p"] - 0.2,
              "market_fair": [m["p"] - m["edge"], 0.2, 1 - m["p"] - 0.2 + m["edge"]],
              "home_odds": m.get("sp", 2.0), "draw_odds": 3.5, "away_odds": 3.5}
             for m in matches]
    (d / "predictions.json").write_text(json.dumps({"predictions": preds}), encoding="utf-8")
    monkeypatch.setattr(ch, "ROOT", tmp_path)

    soon = int((dt.datetime.now(ch.BJT) + dt.timedelta(hours=6)).timestamp())
    board = [{"sid": m["sid"], "home": m["home"], "away": m["away"], "kick_ts": soon,
              "odds": [m["site"], 3.60, 3.60] if site_odds else None}
             for m in matches]
    monkeypatch.setattr(ch.api, "board_with_odds", lambda jwt: board)
    monkeypatch.setattr(ch.api, "my_predictions", lambda jwt: [])
    submitted = []
    monkeypatch.setattr(ch.api, "submit",
                        lambda jwt, sid, mkt, pick: (submitted.append(sid), True)[1])
    return state, submitted


def test_default_gate_is_edge_and_stores_site_price(tmp_path, monkeypatch):
    """不给 policy 就是老口径；但站点价必须随注单落进 records。"""
    state, submitted = _setup(tmp_path, monkeypatch, [
        {"sid": "AAAa", "home": "蒙扎", "away": "卡利亚里", "p": 0.55, "edge": 0.05, "site": 2.20}])
    assert ch.main() == 0
    assert submitted == ["AAAa"]
    rec = json.loads(state.read_text(encoding="utf-8"))["records"][0]
    assert rec["gate"] == "edge"
    assert rec["site_odds"] == pytest.approx(2.2)
    assert rec["ev_site"] == pytest.approx(0.55 * 2.2 - 1, abs=1e-4)


def test_ev_site_gate_admits_a_negative_edge_bet(tmp_path, monkeypatch):
    """E8 的要害：模型与市场分歧为负的场次，只要站点价够软就该合格。"""
    state, submitted = _setup(
        tmp_path, monkeypatch,
        [{"sid": "BBBb", "home": "帕尔马", "away": "都灵", "p": 0.50, "edge": -0.01, "site": 2.10}],
        policy={"gate_mode": "ev_site", "ev_floor": 0.02})
    assert ch.main() == 0
    assert submitted == ["BBBb"], "ev_site=+0.05 应过 2% 档（edge 为负不影响）"
    rec = json.loads(state.read_text(encoding="utf-8"))["records"][0]
    assert rec["gate"] == "ev_site"
    assert rec["ev_site"] == pytest.approx(0.05, abs=1e-4)


def test_ev_site_gate_respects_the_floor(tmp_path, monkeypatch):
    """低于地板就不投——放量靠价差质量，不是靠把门槛压到 0。"""
    _, submitted = _setup(
        tmp_path, monkeypatch,
        [{"sid": "CCCc", "home": "莱切", "away": "乌迪内斯", "p": 0.50, "edge": -0.01, "site": 1.90}],
        policy={"gate_mode": "ev_site", "ev_floor": 0.02})
    assert ch.main() == 0
    assert submitted == []


def test_ev_site_gate_refuses_when_board_has_no_odds(tmp_path, monkeypatch):
    """拿不到站点价时无从判 EV，必须一注不投，不能悄悄退回体彩价。"""
    _, submitted = _setup(
        tmp_path, monkeypatch,
        [{"sid": "DDDd", "home": "昂热", "away": "马赛", "p": 0.55, "edge": 0.05, "site": 2.20}],
        policy={"gate_mode": "ev_site", "ev_floor": 0.02}, site_odds=False)
    assert ch.main() == 0
    assert submitted == []


def test_broken_policy_falls_back_to_edge(tmp_path, monkeypatch):
    """policy 写坏/写错类型 → 保守回默认 edge，不能崩，也不能半切换。"""
    state, submitted = _setup(
        tmp_path, monkeypatch,
        [{"sid": "EEEe", "home": "图卢兹", "away": "南特", "p": 0.55, "edge": 0.05, "site": 2.20}],
        policy={"gate_mode": "ev_site", "ev_floor": "不是数"})
    assert ch.main() == 0
    assert submitted == ["EEEe"]
    assert json.loads(state.read_text(encoding="utf-8"))["records"][0]["gate"] == "edge", \
        "口径切换必须原子：ev_floor 取不到时 gate_mode 也不能已经换掉"


def test_ev_site_gate_refuses_absurd_price(tmp_path, monkeypatch):
    """站点价软到 EV>25% 判为脏数据（外部盘口解析出过错），不能当价值放量。"""
    _, submitted = _setup(
        tmp_path, monkeypatch,
        [{"sid": "GGGg", "home": "斯特拉斯堡", "away": "朗斯", "p": 0.55, "edge": 0.05, "site": 5.00}],
        policy={"gate_mode": "ev_site", "ev_floor": 0.02})
    assert ch.main() == 0
    assert submitted == []


def test_zero_output_names_the_caliber(tmp_path, monkeypatch, capsys):
    """日志要能看出是哪一档拦住的，否则换口径后 duty.log 无法解读。"""
    _setup(tmp_path, monkeypatch,
           [{"sid": "FFFf", "home": "雷恩", "away": "里昂", "p": 0.50, "edge": -0.01, "site": 1.50}],
           policy={"gate_mode": "ev_site", "ev_floor": 0.02})
    ch.main()
    out = capsys.readouterr().out
    assert "ev_site" in out and "2.0%" in out, f"实际输出：{out}"


def test_zero_output_attributes_match_and_candidate_counts(tmp_path, monkeypatch, capsys):
    """E8 护栏②归因：零合格时要能看出匹配几场、候选几场、最高 EV 多少。"""
    _setup(tmp_path, monkeypatch,
           [{"sid": "HHHh", "home": "帕尔马", "away": "都灵", "p": 0.50, "edge": -0.01, "site": 1.90}],
           policy={"gate_mode": "ev_site", "ev_floor": 0.02})
    ch.main()
    out = capsys.readouterr().out
    assert "/匹配1" in out and "/候选1" in out and "最高-5.00%" in out, f"实际输出：{out}"


def test_zero_output_names_floor_blocked_opportunity(tmp_path, monkeypatch, capsys):
    """体彩价<1.60 被旧筛挡住、但站点 EV 达标的场次必须留痕。

    10-22 实盘：萨迪纳摩 vs 奈梅亨 体彩 1.42、站点 1.62、P≈0.647 → ev_site +4.8%。
    不改变投注面（仍不投），只让 E8 的抽样偏差在每轮日志里可见。
    """
    _, submitted = _setup(
        tmp_path, monkeypatch,
        [{"sid": "IIIi", "home": "萨迪纳摩", "away": "奈梅亨", "p": 0.647,
          "edge": -0.0042, "site": 1.62, "sp": 1.42}],
        policy={"gate_mode": "ev_site", "ev_floor": 0.02})
    ch.main()
    out = capsys.readouterr().out
    assert submitted == [], "诊断不得改变投注面"
    assert "/候选0" in out, f"体彩 1.42 必须仍被旧筛挡住：{out}"
    assert "/价筛外1场达标最高+4.8" in out and "萨迪纳摩" in out, f"实际输出：{out}"
