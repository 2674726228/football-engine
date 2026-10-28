"""`njstats_duty.py` mint 失败后自动无头续期 SESSION 的回归测试（2026-10-20 事故）。

事故现场：SESSION cookie 寿命约 20h（10-19 02:08 播种 → 23:35 失效），
10-19 23:35 起连续 5 轮 duty 报 `mint HTTP 401（session 死，需重新播种）`。
而 `mint()` 返回 None 会让 `main()` 提前 `return 20`——**连 challenge_hourly
都不跑**。所以这几轮不是「零合格」，是根本没去看有没有合格场次，
且日志措辞把人引向「必须人工弹浏览器登录」。

实际不需要人工：seed_login 用的持久 profile 里除会话级 SESSION 还存着长效
`remember-me`，无头启动即静默换发新 SESSION，不涉及验证码。

这道测试锁住两件事：
1. mint 拿到非 200 时会先尝试续期，且续期成功后用新 cookie 重试 mint；
2. 续期也失败时，仍按原行为返回 None（不新增静默失败）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "njstats_duty", ROOT / "scripts" / "njstats_duty.py"
)
assert SPEC and SPEC.loader
duty = importlib.util.module_from_spec(SPEC)
sys.modules["njstats_duty"] = duty
SPEC.loader.exec_module(duty)


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def fake_session(tmp_path, monkeypatch):
    """把 SESS 指向 tmp 文件，并记录发给 mint 的 cookie。"""
    sess = tmp_path / "njstats_session.json"
    sess.write_text('{"SESSION": "oldcookie"}', encoding="utf-8")
    monkeypatch.setattr(duty, "SESS", sess)
    # duty.log() 写的是真实 ~/.football-engine/duty.log；不隔离的话跑一次测试
    # 就会往值守日志里塞几行假的「mint 401」，把人引去查不存在的故障。
    monkeypatch.setattr(duty, "LOG", tmp_path / "duty.log")
    seen: list[str] = []

    def _get(url, headers=None, **kw):
        seen.append((headers or {}).get("Cookie", ""))
        # 只有新 cookie 才放行；旧 cookie 一律 401
        if seen[-1] == "SESSION=newcookie":
            return _Resp(200, {"data": {"token": "jwt-ok"}})
        return _Resp(401)

    monkeypatch.setattr(duty.requests, "get", _get)
    return sess, seen


def test_mint_retries_with_fresh_cookie_after_autoheal(fake_session, monkeypatch):
    """401 → 续期脚本把 SESSION 换成新值 → mint 必须用新 cookie 重试并成功。"""
    sess, seen = fake_session

    def _heal():
        sess.write_text('{"SESSION": "newcookie"}', encoding="utf-8")
        return True

    monkeypatch.setattr(duty, "autoheal_session", _heal)
    assert duty.mint() == "jwt-ok"
    assert seen == ["SESSION=oldcookie", "SESSION=newcookie"], "续期后未用新 cookie 重试"


def test_autoheal_called_exactly_once(fake_session, monkeypatch):
    """不能无限重试：一次 401 只触发一次续期。"""
    calls = []
    monkeypatch.setattr(duty, "autoheal_session", lambda: calls.append(1) or True)
    duty.mint()
    assert len(calls) == 1, f"续期被调用 {len(calls)} 次，应恰好 1 次"


def test_autoheal_failure_returns_none(fake_session, monkeypatch):
    """续期也失败时必须维持原行为：返回 None（上层 return 20 提示人工）。"""
    monkeypatch.setattr(duty, "autoheal_session", lambda: False)
    assert duty.mint() is None


def test_success_path_never_touches_autoheal(fake_session, monkeypatch):
    """正常 200 不该触发任何续期（避免每轮无谓启动 chromium）。"""
    monkeypatch.setattr(duty, "autoheal_session", lambda: pytest.fail("200 时不应触发续期"))
    monkeypatch.setattr(duty.requests, "get", lambda *a, **k: _Resp(200, {"data": {"token": "jwt-ok"}}))
    assert duty.mint() == "jwt-ok"
