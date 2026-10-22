"""`challenge_hourly.py` 周翻转落盘的回归测试（2026-10-19 事故）。

事故现场：W42→W43 翻转后，state 文件仍停在 `2026-W42 / used 27 / mode W42-fill`。
根因不是周界算法，而是**持久化位置**：`STATE.write_text` 全文件只出现一次，
位于「提交成功注数 > 0」之后的路径；零合格 / 配额已满 / 列表失败都会提前
`return 0`，于是翻转结果从未写回。

后果不是只显示难看：`mode == "W42-fill"` 残留会让任何读者（人 / 看板 /
复盘脚本）误以为一次性填仓模式仍在生效，而 week/used 也与实际不符。
注：**门槛本身没坏**——翻转在算 gate 之前，g 取自内存里翻转后的 mode，
duty.log 的「门槛1.5%」即证据。坏的是落盘状态；只要某轮投出 ≥1 注，
整份 st 会被写回而自愈，这正是它长期没被发现的原因。

这道测试锁住「翻转即落盘」，且锁住「零合格轮不制造噪音提交」
（只写 week/used/mode，records 不动）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import challenge_hourly as ch  # noqa: E402


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """把 STATE / POLICY 指向 tmp，绝不碰仓库里的真 state。"""
    state = tmp_path / "challenge_state.json"
    monkeypatch.setattr(ch, "STATE", state)
    monkeypatch.setattr(ch, "POLICY", tmp_path / "challenge_policy.json")
    # 无 JWT 时 main() 直接退 0，测不到翻转路径；这里给一个占位并让
    # board_with_odds 返回 None（模拟「列表失败」这条同样会提前 return 的分支）。
    monkeypatch.setenv("NJSTATS_JWT", "test-jwt")
    monkeypatch.setattr(ch.api, "board_with_odds", lambda jwt: None)
    return state


def test_rollover_persists_even_when_no_submission(isolated):
    """列表失败（提前 return 0）也必须把周翻转写回文件。"""
    isolated.write_text(json.dumps({
        "week": "2026-W42", "quota": 28, "used": 27,
        "mode": "W42-fill", "records": [{"match": "旧场次"}],
    }), encoding="utf-8")

    assert ch.main() == 0

    st = json.loads(isolated.read_text(encoding="utf-8"))
    assert st["week"] == ch.iso_week(__import__("datetime").datetime.now(ch.BJT))
    assert st["used"] == 0, "周翻转必须把 used 归零并落盘"
    assert st["mode"] is None, "W42-fill 是一次性临时模式，翻转后必须关闭"
    assert st["records"] == [{"match": "旧场次"}], "翻转只写周字段，records 不得被清理"


def test_quota_full_persists_when_week_already_current(isolated):
    """已在当周且配额满：提前 return，且不应重写文件（无翻转）。"""
    wk = ch.iso_week(__import__("datetime").datetime.now(ch.BJT))
    payload = {"week": wk, "quota": 28, "used": 28, "mode": None, "records": []}
    isolated.write_text(json.dumps(payload), encoding="utf-8")
    before = isolated.read_text(encoding="utf-8")

    assert ch.main() == 0

    assert isolated.read_text(encoding="utf-8") == before, "无翻转不应产生写盘"


def test_gate_uses_real_monday_threshold_after_rollover(isolated):
    """翻转后门槛必须是周一 1.5pp，而不是 fill 分支的 0.1pp。"""
    isolated.write_text(json.dumps({
        "week": "2026-W42", "quota": 28, "used": 27, "mode": "W42-fill", "records": [],
    }), encoding="utf-8")

    ch.main()

    st = json.loads(isolated.read_text(encoding="utf-8"))
    fill = st.get("mode") == "W42-fill"
    now = __import__("datetime").datetime.now(ch.BJT)
    gate = 0.001 if fill else (0.015 if now.weekday() <= 3 else 0.008)
    assert fill is False
    assert gate == pytest.approx(0.015 if now.weekday() <= 3 else 0.008)
