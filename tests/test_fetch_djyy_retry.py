"""DJYY 快照抓取的健壮性测试（2026-10-22）。

上游 Cloudflare 的挑战是**间歇**的：CI 实测同一 URL 6 次里约 2 次 403，
其余 200。旧实现单次请求、失败即返回 None，于是 1/3 的轮次会把
`data/djyy_matches.json` 留在两个月前的状态，而日志与"今天没比赛"同形。

这里锁三件事：
1. 403/5xx 会退避重试；404 这类确定性错误不浪费请求。
2. 抓不到赛程时，必须点名旧快照的日期和年龄，不能静默保留。
3. comparison 全挂时不得写盘——否则会把有效的 djyy_prob 抹成全 null。
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import fetch_djyy_ssr as fds


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(fds.time, "sleep", lambda _s: None)


def test_403_is_retried_then_succeeds(monkeypatch):
    calls = []

    def fake(req, **kw):
        calls.append(req.full_url)
        if len(calls) < 3:
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
        return _Resp({"ok": 1})

    monkeypatch.setattr(fds.urllib.request, "urlopen", fake)
    assert fds._fetch_json("https://x/api") == {"ok": 1}
    assert len(calls) == 3, "403 应重试到成功为止"


def test_403_gives_up_after_attempts(monkeypatch, capsys):
    calls = []

    def fake(req, **kw):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(fds.urllib.request, "urlopen", fake)
    assert fds._fetch_json("https://x/api", attempts=3) is None
    assert len(calls) == 3
    err = capsys.readouterr().err
    assert "HTTP 403" in err, "放弃时必须写出最终状态码"


def test_404_is_not_retried(monkeypatch):
    calls = []

    def fake(req, **kw):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(fds.urllib.request, "urlopen", fake)
    assert fds._fetch_json("https://x/api") is None
    assert len(calls) == 1, "404 重试无意义"


def test_network_error_is_retried(monkeypatch):
    calls = []

    def fake(req, **kw):
        calls.append(req.full_url)
        raise TimeoutError("timed out")

    monkeypatch.setattr(fds.urllib.request, "urlopen", fake)
    assert fds._fetch_json("https://x/api", attempts=2) is None
    assert len(calls) == 2, "网络错误属于瞬时故障，应当重试"


def test_headers_only_carry_key_when_configured(monkeypatch):
    monkeypatch.setenv("DJYY_KEY", "  k  ")
    assert fds._headers()["X-DJYY-Key"] == "k"
    monkeypatch.delenv("DJYY_KEY")
    assert "X-DJYY-Key" not in fds._headers()


def test_empty_fixtures_report_snapshot_age(tmp_path, monkeypatch, capsys):
    out = tmp_path / "djyy_matches.json"
    out.write_text(json.dumps({
        "extracted_at": "2026-08-15T00:00:00+00:00", "with_odds": 47,
        "matches": [{"fs_match_id": 1}],
    }), encoding="utf-8")
    monkeypatch.setattr(fds, "OUTPUT", out)
    monkeypatch.setattr(fds, "fetch_fixtures", lambda *a, **k: [])

    fds.main()

    o = capsys.readouterr().out
    assert "2026-08-15" in o and "天前" in o, f"必须点名旧快照年龄，实际：{o}"
    assert json.loads(out.read_text(encoding="utf-8"))["with_odds"] == 47


def test_all_comparisons_failing_does_not_overwrite(tmp_path, monkeypatch, capsys):
    out = tmp_path / "djyy_matches.json"
    out.write_text(json.dumps({
        "extracted_at": "2026-10-21T00:00:00+00:00", "with_odds": 47, "matches": [],
    }), encoding="utf-8")
    monkeypatch.setattr(fds, "OUTPUT", out)
    monkeypatch.setattr(fds, "fetch_fixtures", lambda *a, **k: [
        {"id": 1, "home": {"name_en": "A"}, "away": {"name_en": "B"}},
    ])
    monkeypatch.setattr(fds, "fetch_comparison", lambda mid: None)

    fds.main()

    o = capsys.readouterr().out
    assert "不覆盖" in o, f"半程失败必须拒绝写盘，实际：{o}"
    assert json.loads(out.read_text(encoding="utf-8"))["with_odds"] == 47
