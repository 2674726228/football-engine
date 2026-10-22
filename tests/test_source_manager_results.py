"""赛果源全挂必须报错，而不是伪装成「今天无赛果」（2026-10-23）。

`SourceManager.fetch_results` 原先对所有源异常都 `continue`，最后返回 []；
结算层看到空列表直接「无新增赛果，快速退出」——账本静默不涨，和 sporttery/DJYY
之前的静默死亡同型。现在只有「没有任何源成功回答」时才 raise。
"""
from datetime import date

import pytest

from engine.sources.manager import SourceManager

D = date(2026, 10, 22)


class _Src:
    def __init__(self, name, result=None, err=None, trust=False):
        self.name = name
        self._result = result
        self._err = err
        self.trust_empty_results = trust

    def fetch_results(self, _d):
        if self._err:
            raise self._err
        return list(self._result or [])


def _mgr(tmp_path, sources):
    m = SourceManager(tmp_path)
    m.sources = sources
    return m


def test_all_sources_fail_raises_with_reasons(tmp_path):
    m = _mgr(tmp_path, [_Src("a", err=RuntimeError("boom")),
                        _Src("b", err=ValueError("bad json"))])
    with pytest.raises(RuntimeError) as ei:
        m.fetch_results(D)
    msg = str(ei.value)
    assert "所有赛果源均失败" in msg and "a:" in msg and "b:" in msg


def test_one_trusted_source_answers_empty_is_a_legit_empty_day(tmp_path):
    m = _mgr(tmp_path, [_Src("a", err=RuntimeError("waf")),
                        _Src("b", result=[], trust=True)])
    assert m.fetch_results(D) == []


def test_untrusted_empty_does_not_mask_failure(tmp_path):
    """500万/ESPN 这类把网络错误吞成 [] 的源，不能证明「今天没赛果」。"""
    m = _mgr(tmp_path, [_Src("a", err=RuntimeError("waf")), _Src("b", result=[])])
    with pytest.raises(RuntimeError):
        m.fetch_results(D)


def test_fallback_returns_later_source_results(tmp_path):
    hit = [object()]
    m = _mgr(tmp_path, [_Src("a", result=[]), _Src("b", result=hit)])
    assert m.fetch_results(D) == hit


def test_early_error_does_not_block_later_hit(tmp_path):
    hit = [object()]
    m = _mgr(tmp_path, [_Src("a", err=RuntimeError("waf")), _Src("b", result=hit)])
    assert m.fetch_results(D) == hit
