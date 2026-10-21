"""duty 并发锁的回归测试（2026-10-22，从 harness/run_duty.sh 搬进脚本）。

壳脚本退役时，如果锁不跟着搬，删掉的就不只是一个 wrapper——
2026-10-21 那次加的并发保护会一起消失。三种情形与当初验壳脚本时一致：
正常跑释放 / 锁被占跳过 / 陈锁清理。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _duty(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "njstats_duty_lock", ROOT / "scripts" / "njstats_duty.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["njstats_duty_lock"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "LOCK", tmp_path / "duty.lock")
    monkeypatch.setattr(mod, "log", lambda m: None)
    return mod


def test_lock_path_is_the_same_for_every_entry_point(monkeypatch):
    """锁路径不能随 TMPDIR 变。

    launchd 的 TMPDIR 是每用户私有目录、Qoder 自动化的又可能不同、
    手工 shell 是第三个值。三个入口各锁各的 = 并发保护形同不存在，
    而这正是这次要防的场景（确定性 duty 与 agent 补位会撞车）。
    """
    monkeypatch.setenv("TMPDIR", "/tmp/whatever-a")
    spec = importlib.util.spec_from_file_location(
        "njstats_duty_lockpath", ROOT / "scripts" / "njstats_duty.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["njstats_duty_lockpath"] = mod
    spec.loader.exec_module(mod)
    assert mod.LOCK == Path.home() / ".football-engine" / "duty.lock", \
        f"锁路径必须固定，实际 {mod.LOCK}"


def test_lock_is_released_after_a_run(monkeypatch, tmp_path):
    duty = _duty(monkeypatch, tmp_path)
    seen = {}

    def fake_main():
        seen["ran"] = True
        assert duty.LOCK.is_dir(), "main() 跑的时候锁必须是持有的"
        return 0
    monkeypatch.setattr(duty, "main", fake_main)

    assert duty._locked_main() == 0
    assert seen.get("ran")
    assert not duty.LOCK.exists(), "跑完要释放，否则下一轮全被当成陈锁"


def test_lock_blocks_a_concurrent_run(monkeypatch, tmp_path):
    duty = _duty(monkeypatch, tmp_path)
    duty.LOCK.mkdir()
    called = []
    monkeypatch.setattr(duty, "main", lambda: called.append(1))

    assert duty._locked_main() == 0
    assert called == [], "锁被占时不能并跑（并发会撞 git pull --rebase 和写 state）"


def test_stale_lock_is_cleaned_up(monkeypatch, tmp_path):
    """进程被 kill -9 不会自己清锁；超过陈限必须自动回收，否则永久卡死。"""
    duty = _duty(monkeypatch, tmp_path)
    duty.LOCK.mkdir()
    old = time.time() - duty.LOCK_STALE_SEC - 60
    os.utime(duty.LOCK, (old, old))
    called = []
    monkeypatch.setattr(duty, "main", lambda: called.append(1) or 0)

    assert duty._locked_main() == 0
    assert called == [1], "陈锁应被清理并照常跑这一轮"


def test_fresh_lock_is_not_treated_as_stale(monkeypatch, tmp_path):
    duty = _duty(monkeypatch, tmp_path)
    duty.LOCK.mkdir()
    called = []
    monkeypatch.setattr(duty, "main", lambda: called.append(1))

    assert duty._locked_main() == 0
    assert called == [], "锁龄很小时是真有人在跑，不能当陈锁清掉"


def test_skip_lock_env_for_manual_runs(monkeypatch, tmp_path):
    duty = _duty(monkeypatch, tmp_path)
    duty.LOCK.mkdir()
    monkeypatch.setenv("DUTY_SKIP_LOCK", "1")
    called = []
    monkeypatch.setattr(duty, "main", lambda: called.append(1) or 7)

    assert duty._locked_main() == 7
    assert called == [1]
    assert duty.LOCK.is_dir(), "跳过锁时不该把别人正在持有的锁删掉"
