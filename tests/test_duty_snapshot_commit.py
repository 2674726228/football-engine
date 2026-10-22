"""duty 提交链的回归测试（2026-10-22）。

钉的是这次审计查出来的实际缺陷：`njstats_duty.py` 里那段内联 commit 只认
`data/state/challenge_state.json` 一个文件，而 duty 每天另外写的三份快照
（site_odds / my_predictions / community）**从来没被提交过**。后果不是
"少一个备份"，是站点赔率样本只活在笔记本工作区——而站点赔率快照存在的
唯一理由就是攒够样本做配对回放（docs/CHALLENGE_ODDS_GAP_20261021.md）。
已知坑②「git reset --hard 清归档」已经发生过两次。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent


def _load_duty():
    spec = importlib.util.spec_from_file_location(
        "njstats_duty", ROOT / "scripts" / "njstats_duty.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["njstats_duty"] = mod
    spec.loader.exec_module(mod)
    return mod


def _fake_sh(status_out="", push_rc=0, calls=None):
    def sh(*args):
        if calls is not None:
            calls.append(args)
        out = status_out if args[:3] == ("git", "status", "--short") else ""
        rc = push_rc if args[:2] == ("git", "push") else 0
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")
    return sh


def test_snapshot_paths_are_all_staged(monkeypatch):
    duty = _load_duty()
    calls: list[tuple] = []
    monkeypatch.setattr(duty, "sh", _fake_sh(
        " M data/state/njstats/site_odds/x.json", calls=calls))
    monkeypatch.setattr(duty, "log", lambda m: None)
    monkeypatch.setattr(duty.time, "sleep", lambda s: None)

    assert duty.commit_push(duty.SNAPSHOT_PATHS, "duty: 快照") is True
    add = next(c for c in calls if c[:2] == ("git", "add"))
    for p in duty.SNAPSHOT_PATHS:
        assert p in add, f"{p} 没进提交链——这正是 2026-10-22 修掉的缺陷"
    assert ("git", "push", "origin", "main") in calls


def test_three_snapshot_dirs_cover_the_duty_outputs(monkeypatch):
    """三份快照必须都在名单里：漏一份就是继续漏攒一天的样本。"""
    duty = _load_duty()
    joined = " ".join(duty.SNAPSHOT_PATHS)
    for d in ("site_odds", "my_predictions", "community"):
        assert d in joined, f"SNAPSHOT_PATHS 缺 {d}/"


def test_clean_tree_does_not_commit(monkeypatch):
    duty = _load_duty()
    calls: list[tuple] = []
    monkeypatch.setattr(duty, "sh", _fake_sh("", calls=calls))
    monkeypatch.setattr(duty, "log", lambda m: None)
    assert duty.commit_push(duty.STATE_PATHS, "duty: state") is True
    assert not [c for c in calls if c[:2] in (("git", "add"), ("git", "commit"))], \
        "零合格轮不该制造噪音提交（这条纪律原先写在 challenge_hourly 的注释里）"


def test_main_wires_the_snapshot_commit(monkeypatch, tmp_path):
    """链路测试：duty 的 main() 真的要用 SNAPSHOT_PATHS 调一次 commit_push。

    单独测 commit_push 只能证明这个函数会干活。本项目的头号病害是
    「写了却从未被调用」（PROJECT.md 2026-10-13 扫出 7 个），所以这条
    接线必须单独钉住——把 main() 里的 commit_push 换成 recorder 驱动一遍。
    """
    duty = _load_duty()
    calls: list[list[str]] = []
    monkeypatch.setattr(duty, "sh", _fake_sh(""))
    monkeypatch.setattr(duty, "log", lambda m: None)
    monkeypatch.setattr(duty, "mint", lambda: "fake-jwt")
    monkeypatch.setattr(duty, "push_secret", lambda t: None)
    monkeypatch.setattr(duty, "JWT_FILE", tmp_path / "njstats_jwt")
    monkeypatch.setattr(duty, "_maybe_server_records_snapshot", lambda: None)
    monkeypatch.setattr(duty, "_maybe_community_snapshot", lambda: None)
    monkeypatch.setattr(duty, "_maybe_djyy_snapshot", lambda: None)
    monkeypatch.setattr(duty, "_maybe_dispatch_ci", lambda n: None)

    def rec_commit(paths, msg):
        calls.append(list(paths))
        return True
    monkeypatch.setattr(duty, "commit_push", rec_commit)
    monkeypatch.setattr(duty.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="challenge: 零合格（edge 门槛1.5%/池236）——正常", stderr="", returncode=0))
    for flag in ("DUTY_SKIP_SERVER_SNAPSHOT", "DUTY_SKIP_COMMUNITY", "DUTY_SKIP_CI_WATCHDOG"):
        monkeypatch.delenv(flag, raising=False)

    assert duty.main() == 0
    assert duty.SNAPSHOT_PATHS in calls, f"main() 没提交快照，实际调用：{calls}"
    assert duty.STATE_PATHS in calls, "state 的提交链不能因为重构而丢"


def test_push_failure_is_visible_and_retried(monkeypatch):
    duty = _load_duty()
    calls: list[tuple] = []
    logged: list[str] = []
    monkeypatch.setattr(duty, "sh", _fake_sh(" M x", calls=calls, push_rc=1))
    monkeypatch.setattr(duty, "log", logged.append)
    monkeypatch.setattr(duty.time, "sleep", lambda s: None)

    assert duty.commit_push(duty.SNAPSHOT_PATHS, "duty: 快照") is False
    assert len([c for c in calls if c[:2] == ("git", "push")]) == 3, "重试三次"
    assert any("push 失败" in m for m in logged), f"失败必须可见，实际日志：{logged}"


# ---------- DJYY 快照（2026-10-22） ----------
#
# 上游对 Worker/CI 出口整体 challenge，只有本机住宅网络能直连。把抓取放在
# duty 里是唯一不依赖第三方代理的路径；CI 消费仓库里的快照。

def _stale_djyy(tmp_path, hours=48):
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc) - timedelta(hours=hours)
    (d / "djyy_matches.json").write_text(json.dumps({
        "extracted_at": ts.isoformat(), "with_odds": 3, "matches": [],
    }), encoding="utf-8")


def test_djyy_snapshot_fetched_directly_when_stale(tmp_path, monkeypatch):
    duty = _load_duty()
    _stale_djyy(tmp_path)
    monkeypatch.setattr(duty, "ROOT", tmp_path)
    monkeypatch.setattr(duty, "DJYY_MARKER", tmp_path / "marker")
    monkeypatch.setattr(duty, "log", lambda m: None)
    runs, commits = [], []
    monkeypatch.setattr(duty.subprocess, "run", lambda *a, **k: (
        runs.append(k), SimpleNamespace(returncode=0, stdout="  ✓ 42 matches", stderr=""))[1])
    monkeypatch.setattr(duty, "commit_push", lambda p, m: commits.append((p, m)) or True)

    duty._maybe_djyy_snapshot()

    assert runs, "快照陈旧时必须发起抓取"
    assert runs[0]["env"]["DJYY_PROXY"] == "", "必须清空代理走本机直连"
    assert "DJYY_KEY" not in runs[0]["env"], "本机直连不需要也不该带 Worker key"
    assert commits == [([duty.DJYY_PATH], "duty: DJYY 快照（本机住宅网络抓取）")]


def test_djyy_snapshot_fresh_is_skipped(tmp_path, monkeypatch):
    duty = _load_duty()
    _stale_djyy(tmp_path, hours=1)
    monkeypatch.setattr(duty, "ROOT", tmp_path)
    monkeypatch.setattr(duty, "DJYY_MARKER", tmp_path / "marker")
    runs = []
    monkeypatch.setattr(duty.subprocess, "run", lambda *a, **k: runs.append(1))

    duty._maybe_djyy_snapshot()

    assert not runs, "10h 内的快照不该重复抓"


def test_djyy_failed_fetch_is_rate_limited(tmp_path, monkeypatch):
    duty = _load_duty()
    _stale_djyy(tmp_path)
    monkeypatch.setattr(duty, "ROOT", tmp_path)
    monkeypatch.setattr(duty, "DJYY_MARKER", tmp_path / "marker")
    monkeypatch.setattr(duty, "log", lambda m: None)
    runs, commits = [], []
    monkeypatch.setattr(duty.subprocess, "run", lambda *a, **k: (
        runs.append(1), SimpleNamespace(returncode=1, stdout="", stderr="HTTP 403"))[1])
    monkeypatch.setattr(duty, "commit_push", lambda p, m: commits.append(p) or True)

    duty._maybe_djyy_snapshot()
    duty._maybe_djyy_snapshot()

    assert len(runs) == 1, "失败后 2h 内不应反复请求上游"
    assert not commits, "没拿到新数据就不该提交"
