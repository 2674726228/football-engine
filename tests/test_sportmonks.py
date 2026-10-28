"""SportMonks 客户端单测（2026-10-11）

HTTP 层全部 mock（_get），只测解析/筛选/限流间隔/权益探针判定逻辑。
"""
import pytest

import engine.sources.sportmonks as sm
from engine.sources.sportmonks import SportMonksClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SPORTMONKS_TOKEN", "test-token")
    c = SportMonksClient(min_interval_s=0)
    return c


def _patch_get(monkeypatch, client, mapping):
    """mapping: path 前缀 → 返回值。未命中路径返回 {'data': []}。"""
    calls = []

    def fake_get(path, params=None, include=None, retries=3):
        calls.append(path)
        for prefix, ret in mapping.items():
            if path.startswith(prefix):
                return ret
        return {"data": []}

    monkeypatch.setattr(client, "_get", fake_get)
    return calls


# ------------------------------------------------------ token 装载

def test_load_token_priority(monkeypatch, tmp_path):
    monkeypatch.setenv("SPORTMONKS_TOKEN", "env-tok")
    assert sm.load_token() == "env-tok"
    monkeypatch.delenv("SPORTMONKS_TOKEN")
    fake = tmp_path / "tok"
    fake.write_text("file-tok", encoding="utf-8")
    monkeypatch.setattr(sm, "TOKEN_FILE", fake)
    assert sm.load_token() == "file-tok"


def test_client_requires_token(monkeypatch):
    monkeypatch.delenv("SPORTMONKS_TOKEN", raising=False)
    monkeypatch.setattr(sm, "TOKEN_FILE", __import__("pathlib").Path("/nonexistent/tok"))
    with pytest.raises(ValueError):
        SportMonksClient()


# ------------------------------------------------------ Pinnacle 筛选

def test_pinnacle_1x2_filters_by_bookmaker_and_label(client, monkeypatch):
    _patch_get(monkeypatch, client, {
        "/bookmakers": {"data": [
            {"id": 1, "name": "Bet365"},
            {"id": 694, "name": "Pinnacle"},
            {"id": 700, "name": "Pinnacle Sports"},
        ]},
        "/odds/pre-match/fixtures/": {"data": [
            {"bookmaker_id": 1, "label": "1X2", "value": "1.85", "updated_at": "2026-10-11 10:00:00"},
            {"bookmaker_id": 694, "label": "1X2", "value": "1.95", "updated_at": "2026-10-11 09:00:00"},
            {"bookmaker_id": 694, "label": "1X2", "value": "1.97", "updated_at": "2026-10-11 11:00:00"},
            {"bookmaker_id": 694, "label": "Over/Under", "value": "2.5", "updated_at": "2026-10-11 11:30:00"},
        ]},
    })
    out = client.pinnacle_1x2(123)
    assert out["value"] == "1.97"          # Pinnacle 且 1X2 的最新一条
    assert out["bookmaker_id"] == 694


def test_pinnacle_1x2_reports_missing_entitlement(client, monkeypatch):
    _patch_get(monkeypatch, client, {
        "/bookmakers": {"data": [{"id": 694, "name": "Pinnacle"}]},
        "/odds/pre-match/fixtures/": {"_status": 403, "_error": "odds not in plan"},
    })
    out = client.pinnacle_1x2(123)
    assert out["error"] == "odds_not_entitled"


def test_pinnacle_1x2_reports_no_pinnacle(client, monkeypatch):
    _patch_get(monkeypatch, client, {
        "/bookmakers": {"data": [{"id": 1, "name": "Bet365"}]},
        "/odds/pre-match/fixtures/": {"data": [{"bookmaker_id": 1, "label": "1X2", "value": "1.85"}]},
    })
    assert client.pinnacle_1x2(123)["error"] == "pinnacle_bookmaker_not_found"


# ------------------------------------------------------ xG 解析

def test_xg_handles_list_and_dict_shapes(client, monkeypatch):
    _patch_get(monkeypatch, client, {
        "/fixtures/": {"data": {"id": 1, "xgfixture": [{"home": 1.4, "away": 1.1}]}},
    })
    assert client.xg(1) == {"home": 1.4, "away": 1.1}


# ------------------------------------------------------ 权益探针

def test_verify_access_marks_entitlements(client, monkeypatch):
    _patch_get(monkeypatch, client, {
        "/leagues": {"data": [{"id": i} for i in range(50)]},
        "/bookmakers": {"data": [{"id": 694, "name": "Pinnacle"}]},
        "/odds/pre-match/fixtures/": {"data": [{"bookmaker_id": 694, "label": "1X2",
                                      "value": "1.9", "updated_at": "x"}]},
        "/predictions/": {"data": [{"id": 1}]},
        "/fixtures/": {"data": {"id": 1, "xgfixture": [{"home": 1.0, "away": 1.0}]}},
        "/lineups/": {"data": [{"id": 1}]},
        "/injuries/": {"data": []},          # 200 但空 → 有权限但该场无伤停
    })
    rep = client.verify_access(probe_fixture_id=1)
    assert rep["token_ok"] is True
    assert rep["checks"]["odds"] == "ok"
    assert rep["checks"]["predictions"] == "ok"
    assert rep["checks"]["xg"] == "ok"
    assert rep["checks"]["lineups"] == "ok"
    assert rep["checks"]["injuries"] == "ok"   # 空列表≠无权限
    assert rep["pinnacle_bookmaker_ids"] == [694]


def test_verify_access_reports_forbidden(client, monkeypatch):
    _patch_get(monkeypatch, client, {
        "/leagues": {"data": [{"id": 1}]},
        "/bookmakers": {"data": []},
        "/odds/fixtures/": {"_status": 403, "_error": "no odds"},
        "/predictions/": {"data": []},
        "/fixtures/": {"data": {"id": 1}},
        "/lineups/": {"data": []},
        "/injuries/": {"data": []},
    })
    rep = client.verify_access(probe_fixture_id=1)
    assert rep["checks"]["odds"].startswith("UNAVAILABLE")
    # 空数据 + 无 bookmaker → pinnacle 未找到也应可见
    assert "pinnacle_bookmaker_ids" in rep
