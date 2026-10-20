"""挑战提交「服务端去重」的回归测试（2026-10-21）。

**为什么必须做服务端去重**：CI（challenge.yml）与本机（duty）是两个真正
独立的仓库副本，各自 pull/push 同一份 `data/state/challenge_state.json`。
两边同时跑到同一个候选时，谁先 push 谁赢，另一个仍会按自己那份旧 state
再投一遍。`challenge_hourly.py` 的 `K = min(6, remaining)` 意味着一轮最多
6 注，重复就是实打实的 6 注——竞彩挑战每周只有 28 次机会。

实测缺口真实存在（2026-10-21）：服务端 msgId=11 返回 33 条 vs 本地 state
28 条，差 5 条（朗斯vs葡萄牙体育、比利亚雷亚尔vs那不勒斯、
莱比锡vs埃因霍温、阿森纳vs里尔、马竞vs曼联）——都是历史 state 回写漏记。

**这组测试直接驱动 main()**，不是复刻逻辑。

> 教训：本文件第一版把合并逻辑抄了一份来测，结果把 main() 里的整段去重
> 代码删掉，测试依然全绿——那是在测我自己写的副本。现在改为
> monkeypatch `api.my_predictions` / `api.board_items` / `api.submit`，
> 断言真实代码路径上的行为。
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

ch = importlib.import_module("challenge_hourly")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离 STATE/POLICY，伪造板子/服务端列表/提交，记录真实提交了什么。"""
    state = tmp_path / "challenge_state.json"
    monkeypatch.setattr(ch, "STATE", state)
    monkeypatch.setattr(ch, "POLICY", tmp_path / "challenge_policy.json")
    monkeypatch.setenv("NJSTATS_JWT", "test-jwt")

    submitted: list[tuple[str, int, int]] = []
    monkeypatch.setattr(ch.api, "submit",
                        lambda jwt, sid, mkt, pick: (submitted.append((sid, mkt, pick)), True)[1])
    return state, submitted


def _write_predictions(tmp_path, matches, monkeypatch):
    """造一份当天 predictions.json，并让 ROOT 指到 tmp。"""
    import datetime as _dt

    day = _dt.datetime.now(ch.BJT).strftime("%Y-%m-%d")
    d = tmp_path / "data" / "daily" / day
    d.mkdir(parents=True, exist_ok=True)
    preds = []
    for m in matches:
        preds.append({
            "home_team": m["home"], "away_team": m["away"],
            "home_win_prob": m["p"], "draw_prob": 0.2,
            "away_win_prob": 1 - m["p"] - 0.2,
            "market_fair": [m["p"] - m["edge"], 0.2, 1 - m["p"] - 0.2 + m["edge"]],
            "home_odds": m.get("odds", 2.0), "draw_odds": 3.5, "away_odds": 3.5,
        })
    (d / "predictions.json").write_text(json.dumps({"predictions": preds}), encoding="utf-8")
    monkeypatch.setattr(ch, "ROOT", tmp_path)


def _board(matches):
    import datetime as _dt
    soon = (_dt.datetime.now(ch.BJT) + _dt.timedelta(hours=6)).timestamp()
    return [{"sid": m["sid"], "home": m["home"], "away": m["away"], "kick_ts": int(soon)}
            for m in matches]


def test_server_side_dedupe_blocks_resubmission(tmp_path, monkeypatch, env):
    """核心断言：服务端已预测过的场次，main() 不能再提交。"""
    state, submitted = env
    matches = [
        {"sid": "AAAa", "home": "蒙扎", "away": "卡利亚里", "p": 0.55, "edge": 0.05},
        {"sid": "BBBb", "home": "帕尔马", "away": "都灵", "p": 0.55, "edge": 0.05},
    ]
    _write_predictions(tmp_path, matches, monkeypatch)
    monkeypatch.setattr(ch.api, "board_items", lambda jwt: _board(matches))
    # 服务端说「蒙扎那场我已经预测过了」
    monkeypatch.setattr(ch.api, "my_predictions",
                        lambda jwt: [{"sid": "AAAa", "home": "蒙扎", "away": "卡利亚里"}])

    assert ch.main() == 0
    sids = [s[0] for s in submitted]
    assert "AAAa" not in sids, "服务端已预测过的场次被重复提交了"
    assert "BBBb" in sids, "未预测过的场次应该正常提交"


def test_without_server_list_falls_back_to_local_state(tmp_path, monkeypatch, env):
    """取不到服务端列表时，仍按本地 state 去重（不能崩、不能全放过）。"""
    state, submitted = env
    state.write_text(json.dumps({
        "week": ch.iso_week(__import__("datetime").datetime.now(ch.BJT)),
        "quota": 28, "used": 0, "mode": None,
        "records": [{"match": "蒙扎 vs 卡利亚里"}],
    }), encoding="utf-8")
    matches = [
        {"sid": "AAAa", "home": "蒙扎", "away": "卡利亚里", "p": 0.55, "edge": 0.05},
        {"sid": "BBBb", "home": "帕尔马", "away": "都灵", "p": 0.55, "edge": 0.05},
    ]
    _write_predictions(tmp_path, matches, monkeypatch)
    monkeypatch.setattr(ch.api, "board_items", lambda jwt: _board(matches))
    monkeypatch.setattr(ch.api, "my_predictions", lambda jwt: None)  # 取不到

    assert ch.main() == 0
    sids = [s[0] for s in submitted]
    assert "AAAa" not in sids, "本地 state 里有的场次不该再投"
    assert "BBBb" in sids


def test_server_list_marks_state_gap_in_output(tmp_path, monkeypatch, env, capsys):
    """state 漏记时要在输出里留痕（不然没人知道去重被服务端救过）。"""
    env
    matches = [{"sid": "AAAa", "home": "蒙扎", "away": "卡利亚里", "p": 0.55, "edge": 0.05}]
    _write_predictions(tmp_path, matches, monkeypatch)
    monkeypatch.setattr(ch.api, "board_items", lambda jwt: _board(matches))
    monkeypatch.setattr(ch.api, "my_predictions",
                        lambda jwt: [{"sid": "ZZZz", "home": "朗斯", "away": "葡萄牙体育"}])

    ch.main()
    out = capsys.readouterr().out
    assert "本地 state 没记" in out, f"应在输出里提示 state 缺口，实际输出：{out}"
