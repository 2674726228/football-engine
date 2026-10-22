"""DJYY comparison 必须并发抓（2026-10-23 超时事故）。

串行 200 场 × 单请求 4~26s，duty 的 600s 超时稳定触发：00:10 实测卡满 10 分钟
后放弃，旧快照继续用（12h 未更新）。并发后按输入顺序返回，失败计数不变。
"""
import importlib.util
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "scripts" / "fetch_djyy_ssr.py"


def _load():
    spec = importlib.util.spec_from_file_location("fetch_djyy_ssr_par", SPEC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_enrich_is_concurrent_and_order_preserved(monkeypatch):
    mod = _load()
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def fake_fetch(mid):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return None if mid == 7 else {"id": mid}

    monkeypatch.setattr(mod, "fetch_comparison", fake_fetch)
    monkeypatch.setattr(mod, "extract_match",
                        lambda fx, comp: {"id": fx["id"],
                                          "home_odds_djyy": comp is not None})

    fixtures = [{"id": i} for i in range(1, 21)] + [{"no_id": True}]
    matches, enriched, failed = mod.enrich_matches(fixtures, workers=5)

    assert [m["id"] for m in matches] == list(range(1, 21)), "输出顺序必须与输入一致"
    assert enriched == 19 and failed == 1
    assert state["peak"] >= 2, "串行执行——超时问题没有解决"
    assert state["peak"] <= 5, f"并发超过 workers: {state['peak']}"


def test_workers_env_override(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "fetch_comparison", lambda mid: None)
    monkeypatch.setattr(mod, "extract_match",
                        lambda fx, comp: {"home_odds_djyy": False})
    monkeypatch.setenv("DJYY_FETCH_WORKERS", "2")
    matches, enriched, failed = mod.enrich_matches([{"id": 1}, {"id": 2}])
    assert len(matches) == 2 and enriched == 0 and failed == 2
