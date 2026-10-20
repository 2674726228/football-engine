"""CI 看门狗的回归测试（2026-10-21）。

背景：GitHub 对公开仓库的定时任务限流严重，实测 Challenge workflow 24h
只跑了 3/24 次。本机 duty 每 :05/:35 稳跑，所以链路没断；但 CI 侧的
补位通道形同虚设。看门狗在「CI 很久没跑 + 本机这轮零提交」时手动 dispatch。

**为什么必须锁死约束**：CI 与本机是两个真正独立的仓库副本，去重全靠
同一份 `data/state/challenge_state.json` 的 records。若看门狗在本地刚投完
注时又触发 CI，CI 拿到的 state 可能还没同步到本地刚写的记录 → 同一场被
重复下单。所以下面每条约束都是防重复投注的，不是防「多跑一次」。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("njstats_duty", ROOT / "scripts" / "njstats_duty.py")
assert SPEC and SPEC.loader
duty = importlib.util.module_from_spec(SPEC)
sys.modules["njstats_duty"] = duty
SPEC.loader.exec_module(duty)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """把 HOME 指到 tmp，并记录 dispatch 调用。"""
    monkeypatch.setattr(duty, "HOME", tmp_path)
    real_dir = tmp_path / ".football-engine"
    real_dir.mkdir(exist_ok=True)
    marker = real_dir / ".ci_watchdog_last"
    dispatched = []
    monkeypatch.setattr(duty, "_dispatch_ci_challenge", lambda: (dispatched.append(1), True)[1])
    monkeypatch.setattr(duty, "log", lambda m: None)
    return marker, dispatched


def test_dispatches_when_ci_stale_and_nothing_submitted(env, monkeypatch):
    marker, dispatched = env
    monkeypatch.setattr(duty, "_ci_last_run_age_h", lambda: 5.0)
    duty._maybe_dispatch_ci(0)
    assert len(dispatched) == 1
    assert marker.exists(), "补触发后应写 marker"


def test_dispatches_even_when_local_submitted(env, monkeypatch):
    """本地这轮投了注也照补——去重已改走服务端真值，不会重复下单。

    2026-10-21：challenge_hourly 现在用 api.my_predictions（msgId=11
    账号私有「我已预测」列表）并入去重名单，CI 与本机并发时后到者会
    看到服务端记录而跳过。所以这道闸撤了。
    """
    _, dispatched = env
    monkeypatch.setattr(duty, "_ci_last_run_age_h", lambda: 10.0)
    duty._maybe_dispatch_ci(3)
    assert len(dispatched) == 1


def test_skips_when_ci_recent(env, monkeypatch):
    """CI 只 1 小时没跑（阈值 3h）→ 不补，避免正常调度时叠加。"""
    _, dispatched = env
    monkeypatch.setattr(duty, "_ci_last_run_age_h", lambda: 1.0)
    duty._maybe_dispatch_ci(0)
    assert dispatched == []


def test_skips_when_age_unknown(env, monkeypatch):
    """取不到 CI 状态（无 token/网络/限流）→ 保守不补。"""
    _, dispatched = env
    monkeypatch.setattr(duty, "_ci_last_run_age_h", lambda: None)
    duty._maybe_dispatch_ci(0)
    assert dispatched == []


def test_at_most_once_per_hour(env, monkeypatch):
    """3 小时空档里会有 6 轮 duty，不能每轮都触发（否则堆一串并发 run）。"""
    _, dispatched = env
    monkeypatch.setattr(duty, "_ci_last_run_age_h", lambda: 5.0)
    duty._maybe_dispatch_ci(0)
    duty._maybe_dispatch_ci(0)
    duty._maybe_dispatch_ci(0)
    assert len(dispatched) == 1, f"一小时内应只补一次，实际 {len(dispatched)} 次"


def test_threshold_constant_is_sane():
    """阈值别被随手改成 0（那会把看门狗变成每轮都触发的噪声源）。"""
    assert duty.CI_STALE_H >= 1.0
    assert duty.CI_WORKFLOW_ID > 0
