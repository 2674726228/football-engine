"""DJYY 快照通道的状态可见性（2026-10-28）

背景：`fetch_djyy_ssr.py` 失败时只往 stdout 打一行 ⚠，而 workflow 里是
`python scripts/fetch_djyy_ssr.py || echo "⚠ ..."` —— CI 恒绿。实时源被 403、
快照又抓不到时两条通道同时死掉，外部只能翻日志。

这里锁四件事：
1. 抓取成功/失败都要落 `djyy_snapshot_status.json`，且带上**盘上**快照的真实年龄。
2. 失败不能覆盖已有快照（原有语义，防止把有效 djyy_prob 抹成 null）。
3. 抛异常也要留状态文件——否则"脚本崩了"和"当天没比赛"在 CI 里长得一样。
4. 状态文件跟着 OUTPUT 走，测试指到 tmp_path 时不污染仓库。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import fetch_djyy_ssr as fds


def _status(out: Path) -> dict:
    return json.loads((out.parent / "djyy_snapshot_status.json").read_text(encoding="utf-8"))


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    out = tmp_path / "djyy_matches.json"
    out.write_text(json.dumps({
        "extracted_at": "2026-10-21T00:00:00+00:00",
        "with_odds": 47,
        "matches": [{"id": 1}],
    }), encoding="utf-8")
    monkeypatch.setattr(fds, "OUTPUT", out)
    return out


def test_empty_fixtures_writes_failed_status(snapshot, monkeypatch):
    monkeypatch.setattr(fds, "fetch_fixtures", lambda *a, **k: [])

    fds.main()

    st = _status(snapshot)
    assert st["ok"] is False
    assert st["reason"] == "fixtures_empty"
    assert st["snapshot_age_h"] is not None, "失败时也要报盘上快照的真实年龄"
    assert json.loads(snapshot.read_text(encoding="utf-8"))["with_odds"] == 47


def test_comparison_failure_writes_reason_and_keeps_snapshot(snapshot, monkeypatch):
    monkeypatch.setattr(fds, "fetch_fixtures", lambda *a, **k: [
        {"id": 1, "home": {"name_en": "A"}, "away": {"name_en": "B"}},
    ])
    monkeypatch.setattr(fds, "fetch_comparison", lambda mid: None)

    fds.main()

    st = _status(snapshot)
    assert st["ok"] is False
    assert st["reason"].startswith("comparison_failed")
    assert json.loads(snapshot.read_text(encoding="utf-8"))["with_odds"] == 47


def test_success_writes_counts(snapshot, monkeypatch):
    monkeypatch.setattr(fds, "fetch_fixtures", lambda *a, **k: [{"id": 1}])
    monkeypatch.setattr(fds, "enrich_matches",
                        lambda fixtures, workers=None: ([{"id": 1}], 1, 0))

    fds.main()

    st = _status(snapshot)
    assert st["ok"] is True
    assert st["matches"] == 1 and st["with_odds"] == 1
    assert st["snapshot_age_h"] is not None
    assert json.loads(snapshot.read_text(encoding="utf-8"))["with_odds"] == 1


def test_exception_still_leaves_status(snapshot, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("dns 挂了")

    monkeypatch.setattr(fds, "fetch_fixtures", boom)

    with pytest.raises(RuntimeError):
        fds.main()

    st = _status(snapshot)
    assert st["ok"] is False
    assert "exception RuntimeError" in st["reason"]
