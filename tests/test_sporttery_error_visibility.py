"""sporttery 主数据源的失败可见性（2026-10-13）。

背景：sporttery 是竞彩官方主源（priority=1）。它的 fetch_fixtures /
fetch_results 原来是 `except Exception: return []`，被 WAF 拦（403）、
代理没配、结构变更三种病因在下游全部表现为"今天没数据"——结算静默跳过、
账本静默不涨。与 DJYY 静默 3.5 周（2026-10-11 发现）同病。

修法仿 djyy.LAST_ERROR：失败时记录 kind/detail/at/proxy_configured，
让"拿不到"与"确实没在售"可区分。本文件锁住这个区分。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.sources import sporttery as sp  # noqa: E402


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def json(self):
        return self._payload


@pytest.fixture
def src():
    sp.LAST_ERROR.clear()
    return sp.SportterySource()


def _fail_with(exc):
    def _get(*a, **k):
        raise exc
    return _get


def test_http_error_recorded_as_waf_block(src, monkeypatch):
    """403 必须记成 blocked_by_waf——它是拦截，不是"没在售"。

    历史上 WAF 拦截与空响应在账本上长得一样，这是 DJYY 静默 3.5 周的
    同型病。修好之后消费方（manager/doctor）能区分两者。
    """
    monkeypatch.setattr(sp._SESSION, "get",
                        _fail_with(requests.HTTPError("403 Client Error")))
    assert src.fetch_results(date(2026, 10, 13)) == []
    assert sp.LAST_ERROR.get("kind") == "blocked_by_waf"
    assert "403" in sp.LAST_ERROR.get("detail", "")


def test_network_error_recorded_as_fetch_failed(src, monkeypatch):
    """网络错误不能也报 WAF——病因不同处置不同。"""
    monkeypatch.setattr(sp._SESSION, "get",
                        _fail_with(requests.ConnectionError("reset")))
    assert src.fetch_fixtures(date(2026, 10, 13)) == []
    assert sp.LAST_ERROR.get("kind") == "fetch_failed"


def test_error_record_has_timestamp_and_proxy_flag(src, monkeypatch):
    """记录必须带时间戳与 proxy 配置状态（排查时先知道走没走代理）。"""
    monkeypatch.setattr(sp._SESSION, "get",
                        _fail_with(requests.Timeout("timed out")))
    src.fetch_results(date(2026, 10, 13))
    assert sp.LAST_ERROR.get("at"), "缺时间戳"
    assert "proxy_configured" in sp.LAST_ERROR


def test_success_clears_previous_error(src, monkeypatch):
    """成功后必须清掉旧错误——否则一次历史故障会永久挂红。"""
    monkeypatch.setattr(sp._SESSION, "get",
                        _fail_with(requests.HTTPError("403")))
    src.fetch_results(date(2026, 10, 13))
    assert sp.LAST_ERROR
    # 换成成功响应
    monkeypatch.setattr(sp._SESSION, "get",
                        lambda *a, **k: _Resp(200, {"value": {"matchInfoList": []}}))
    src.fetch_fixtures(date(2026, 10, 13))
    assert sp.LAST_ERROR == {}, "成功后旧错误未清"


def test_empty_but_200_is_not_an_error(src, monkeypatch):
    """200 + 空列表 = 真的没在售（如休市日），不能记错误。

    这条是区分性的反面：「今天确实没比赛」必须保持静默合法，
    否则休市日天天误报。
    """
    monkeypatch.setattr(
        sp._SESSION, "get",
        lambda *a, **k: _Resp(200, {"value": {"matchResultList": []}}))
    assert src.fetch_results(date(2026, 12, 25)) == []
    assert sp.LAST_ERROR == {}, "正常空响应被误记为错误"
