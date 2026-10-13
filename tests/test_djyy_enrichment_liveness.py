"""DJYY enrichment 活性回归测试（2026-10-11）

背景：实测 v2 链 529 场 djyy_prob 命中率为 0，但 config 仍给 djyy 0.2 名义权重。
追查发现两个**互相独立**的缺陷：

  缺陷 A（时间预算）：DJYY_ENRICH_TIME_BUDGET 固定 180s，原注释假设每接口 1.5s，
    实测 5.4s/接口 → 21.8s/场 → 180s 只够 8 场。而账本实测日均 12.1 场、
    峰值 30 场 → 典型日 1/3、峰值日 73% 的场次永远拿不到 enrichment。
    且原实现 `break` 是**静默**的。

  缺陷 B（逐场异常）：`except Exception: continue` 把逐场失败全部吞掉，
    加上 fetch_fixtures 返回 0 时直接 `return {}`，整条链路失效时零告警。

这些测试锁住修复后的行为：预算自适应、截断可见、失败可见、状态落盘。
"""
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.sources import manager as mgr  # noqa: E402


# ---------- 预算自适应 ----------

def test_budget_scales_with_match_count():
    """场次越多预算越大，不能再是固定 180s。"""
    small = mgr.djyy_time_budget(5)
    mid = mgr.djyy_time_budget(15)
    big = mgr.djyy_time_budget(30)
    assert mid > small
    assert big > mid


def test_budget_covers_observed_latency():
    """实测 21.8s/场。典型日 12 场、峰值日 30 场必须被预算完全覆盖。"""
    per_match_observed = 21.8
    for n in (10, 12, 20, 30):
        assert mgr.djyy_time_budget(n) >= n * per_match_observed, \
            f"{n} 场在实测延迟下会被截断"


def test_budget_has_floor_and_ceiling():
    """有下限（空跑不忙等）和上限（第三方卡死不能拖垮流水线）。"""
    assert mgr.djyy_time_budget(0) == mgr.DJYY_ENRICH_MIN_BUDGET
    assert mgr.djyy_time_budget(10000) == mgr.DJYY_ENRICH_MAX_BUDGET


def test_budget_respects_env(monkeypatch):
    monkeypatch.setenv("DJYY_ENRICH_MIN_BUDGET_S", "600")
    monkeypatch.setenv("DJYY_ENRICH_MAX_BUDGET_S", "1200")
    import importlib
    mod = importlib.reload(mgr)
    try:
        assert mod.djyy_time_budget(1) == 600
        assert mod.djyy_time_budget(10000) == 1200
    finally:
        monkeypatch.undo()
        importlib.reload(mod)


# ---------- 失败必须可见 ----------

def test_fetch_failure_writes_status_not_silent(tmp_path):
    """fetch_fixtures 抛异常时，必须落盘错误状态并返回空，
    而不是悄无声息地让 djyy 权重变成死权重。"""
    class Boom(mgr.DJYYSource):
        def fetch_fixtures(self, target_date):
            raise RuntimeError("network unreachable")

        def fetch_match_comparison(self, i):
            return None

    m = mgr.SourceManager(tmp_path)
    m._djyy = Boom()
    out = m.enrich_from_djyy([], __import__("datetime").date(2026, 10, 12))

    assert out == {}
    status = json.loads((tmp_path / "djyy_status.json").read_text(encoding="utf-8"))
    assert status["healthy"] is False
    assert status["error"].startswith("fetch_fixtures:")
    assert "network unreachable" in status["error"]


def test_empty_fixtures_writes_status(tmp_path):
    """返回 0 场（CI 侧真实故障模式）也必须落状态，不能只 print。"""
    class Empty(mgr.DJYYSource):
        def fetch_fixtures(self, target_date):
            return []

        def fetch_match_comparison(self, i):
            return None

    m = mgr.SourceManager(tmp_path)
    m._djyy = Empty()
    m.enrich_from_djyy([], __import__("datetime").date(2026, 10, 12))

    status = json.loads((tmp_path / "djyy_status.json").read_text(encoding="utf-8"))
    assert status["healthy"] is False
    assert status["error"] == "fetch_fixtures_empty"


def test_budget_exhaustion_is_marked(tmp_path, monkeypatch):
    """预算耗尽必须写进状态文件并置 unhealthy——原实现是静默 break。"""
    monkeypatch.setenv("DJYY_ENRICH_MIN_BUDGET_S", "0")
    monkeypatch.setenv("DJYY_ENRICH_SEC_PER_MATCH", "0")
    import importlib
    mod = importlib.reload(mgr)
    try:
        class FakeFixture:
            def __init__(self, i):
                self.match_id = f"m{i}"
                self.home_team = f"H{i}"
                self.away_team = f"A{i}"

        class Slow(mgr.DJYYSource):
            def fetch_fixtures(self, target_date):
                return [FakeFixture(1)]

            def fetch_match_comparison(self, i):
                return {"model": {"p_home": .4, "p_draw": .3, "p_away": .3},
                        "bookmaker": {}}

        m = mod.SourceManager(tmp_path)
        m._djyy = Slow()
        mod.DJYY_ENRICH_MIN_BUDGET = -1.0   # 强制过期
        fixtures = [FakeFixture(1), FakeFixture(2)]
        m.enrich_from_djyy(fixtures, __import__("datetime").date(2026, 10, 12))
        status = json.loads((tmp_path / "djyy_status.json").read_text(encoding="utf-8"))
        assert status["budget_exhausted"] is True
        assert status["healthy"] is False
        assert "truncated_at" in status["detail"]
    finally:
        monkeypatch.undo()
        importlib.reload(mod)


def test_status_coverage_healthy_when_full(tmp_path):
    """全场覆盖时应判为 healthy，供 health-check workflow 消费。"""
    class FakeFixture:
        def __init__(self, i):
            self.match_id = f"m{i}"
            self.home_team = f"H{i}"
            self.away_team = f"A{i}"
            self._djyy_id = 1000 + i          # 匹配表靠它建，缺了就永远匹配不上

    class Ok(mgr.DJYYSource):
        def fetch_fixtures(self, target_date):
            return [FakeFixture(1), FakeFixture(2)]

        def fetch_match_comparison(self, i):
            return {"model": {"p_home": .4, "p_draw": .3, "p_away": .3},
                    "bookmaker": {}}
        def fetch_team_form(self, i, limit=5):
            return {"available": False}
        def fetch_match_info(self, i):
            return {"available": False}

    m = mgr.SourceManager(tmp_path)
    m._djyy = Ok()
    out = m.enrich_from_djyy([FakeFixture(1), FakeFixture(2)],
                             __import__("datetime").date(2026, 10, 12))
    assert len(out) == 2
    status = json.loads((tmp_path / "djyy_status.json").read_text(encoding="utf-8"))
    assert status["coverage"] == 1.0
    assert status["healthy"] is True
    assert status["detail"]["enriched"] == 2


# ---------- 阵容开关 ----------

def test_lineups_flag_default_off():
    """阵容是 store-only，默认关闭以省掉每场 1/4 的接口开销。"""
    assert mgr.DJYY_FETCH_LINEUPS is False


def test_lineups_flag_env(monkeypatch):
    monkeypatch.setenv("DJYY_FETCH_LINEUPS", "1")
    import importlib
    mod = importlib.reload(mgr)
    try:
        assert mod.DJYY_FETCH_LINEUPS is True
    finally:
        monkeypatch.undo()
        importlib.reload(mod)


# ---------- 健康阈值口径（2026-10-13 修正）----------

class _PoolFixture:
    """DJYY 池里的比赛（带 _djyy_id，供 djyy_map 建立映射）。"""
    def __init__(self, i, home=None, away=None):
        self.match_id = f"djyy_{i}"
        self.home_team = home or f"H{i}"
        self.away_team = away or f"A{i}"
        self._djyy_id = 1000 + i


class _OkSource(mgr.DJYYSource):
    """池子有数据、comparison 正常返回的 DJYY 源。"""
    def __init__(self, pool):
        self._pool = pool
    def fetch_fixtures(self, target_date):
        return self._pool
    def fetch_match_comparison(self, i):
        return {"model": {"p_home": .4, "p_draw": .3, "p_away": .3}, "bookmaker": {}}
    def fetch_team_form(self, i, limit=5):
        return {"available": False}
    def fetch_match_info(self, i):
        return {"available": False}


def test_league_coverage_does_not_make_unhealthy(tmp_path):
    """联赛覆盖面差异不该判不健康——这是 2026-10-13 修正的核心。

    实测当天：12 场预测里 5 场是亚冠/英冠（DJYY 池里没有），
    7 场欧冠全部富化成功。旧口径 coverage = 7/12 = 0.58 < 0.8
    → **永远 healthy=false**，告警变成狼来了。

    新口径：healthy 只看 enrich_success（匹配上的场次里富化成功的比例）。
    """
    # 池子有 2 场，我们预测 3 场（第 3 场 DJYY 不覆盖）
    pool = [_PoolFixture(1), _PoolFixture(2)]
    ours = [_PoolFixture(1), _PoolFixture(2),
            _PoolFixture(9, home="某亚冠队", away="另一队")]

    m = mgr.SourceManager(tmp_path)
    m._djyy = _OkSource(pool)
    m.enrich_from_djyy(ours, __import__("datetime").date(2026, 10, 13))
    status = json.loads((tmp_path / "djyy_status.json").read_text(encoding="utf-8"))

    # 匹配上的 2 场全部成功 → enrich_success = 1.0 → 健康
    assert status["detail"]["matched"] == 2, status["detail"]
    assert status["enrich_success"] == 1.0
    assert status["healthy"] is True, "联赛覆盖差异被误判为不健康（旧口径的 bug）"
    # 旧口径 coverage = 2/3 < 0.8，仍然记录但不决定健康
    assert status["coverage"] < 0.8
    assert "league_coverage" in status


def test_upstream_empty_pool_is_unhealthy(tmp_path):
    """DJYY 池返回 0 场 = 上游故障，必须判不健康。

    这是 2026-10-11 事故的形态：529 场 djyy_prob 全 null 却无人察觉。
    matched=0 有两种成因，不能一律当健康：
      pool == 0        → 上游空/被拦 → 不健康
      pool > 0, 无交集 → 联赛差异   → 健康
    """
    m = mgr.SourceManager(tmp_path)
    m._djyy = _OkSource([])          # 池子为空
    m.enrich_from_djyy([], __import__("datetime").date(2026, 10, 13))
    status = json.loads((tmp_path / "djyy_status.json").read_text(encoding="utf-8"))
    assert status["enrich_success"] == 0.0
    assert status["healthy"] is False, "上游池为空必须判不健康"


def test_pool_nonempty_but_no_overlap_is_healthy(tmp_path):
    """池子有数据、只是与我们无交集 → 健康（无富化机会，不是故障）。"""
    pool = [_PoolFixture(1), _PoolFixture(2)]
    ours = [_PoolFixture(7, home="无关队", away="也无关")]

    m = mgr.SourceManager(tmp_path)
    m._djyy = _OkSource(pool)
    m.enrich_from_djyy(ours, __import__("datetime").date(2026, 10, 13))
    status = json.loads((tmp_path / "djyy_status.json").read_text(encoding="utf-8"))
    assert status["detail"]["matched"] == 0
    assert status["healthy"] is True, "有池子但无交集不该判故障"
    assert status["league_coverage"] == 0.0


def test_status_keeps_backward_compatible_coverage(tmp_path):
    """旧字段 coverage 必须保留（dashboard/已有消费方在读）。"""
    m = mgr.SourceManager(tmp_path)
    m._djyy = _OkSource([])
    m.enrich_from_djyy([], __import__("datetime").date(2026, 10, 13))
    status = json.loads((tmp_path / "djyy_status.json").read_text(encoding="utf-8"))
    for k in ("coverage", "covered", "fixtures", "budget_exhausted", "detail"):
        assert k in status, f"旧字段 {k} 丢失，会打破现有消费方"
