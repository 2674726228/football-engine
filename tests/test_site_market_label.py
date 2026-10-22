"""issue #1 显示层修复：竞彩未开售/未确认在售的串票只作参考。

不改选腿、EV、账本——只把「正EV 推荐」降级为「仅参考」，并给腿加来源提示。
站点在售状态来自 duty 入库的 site_odds 快照；没有当日快照时保持原标注（无法验证）。
"""
import json

import engine.build_site as bs

D = "2026-10-22"


def _pred(mid, home, away):
    return {"match_id": mid, "home_team": home, "away_team": away,
            "home_win_prob": 0.5, "draw_prob": 0.3, "away_win_prob": 0.2}


def _leg(mid, home, away):
    return {"match_id": mid, "match": "2026-10-22_周四001",
            "home": home, "away": away, "league": "测试",
            "sel": "home", "odds": 2.0, "prob": 0.6,
            "cal_prob": 0.55, "hit_prob": 0.55, "source": "fusion"}


def _ticket(leg):
    return {"parlay": [{
        "type": "单关", "source": "calibrated", "recommended": True,
        "legs": [leg], "stake": 10, "total_odds": 2.0, "hit_prob_cal": 0.55,
        "cal_ev": 1.0, "cal_roi": 0.1, "potential": 20,
    }], "parlay_calibration": {}}


def _seed(tmp_path, monkeypatch, *, date=D, odds_delta=[]):
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    d = tmp_path / "data/state/njstats/site_odds"
    d.mkdir(parents=True)
    (d / f"{date}.json").write_text(json.dumps({"matches": [
        {"home": "Alpha", "away": "Beta", "odds": [2.0, 3.0, 4.0]},
        {"home": "Gamma", "away": "Delta", "odds": odds_delta},
    ]}), encoding="utf-8")


def test_status_ok_no_spf_and_missing(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    preds = [_pred(f"{D}_a", "Alpha", "Beta"),
             _pred(f"{D}_b", "Gamma", "Delta"),
             _pred(f"{D}_c", "Eps", "Zeta")]
    status, dates = bs._site_spf_status(preds)
    assert status[f"{D}_a"] == "ok"
    assert status[f"{D}_b"] == "no_spf"
    assert f"{D}_c" not in status
    assert dates == {D}


def test_recommended_kept_when_market_open(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    preds = [_pred(f"{D}_a", "Alpha", "Beta")]
    html = bs._parlay_section(_ticket(_leg(f"{D}_a", "Alpha", "Beta")), preds)
    assert "正EV 推荐" in html and "⚠ 仅参考" not in html


def test_not_open_downgrades_to_reference(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    preds = [_pred(f"{D}_b", "Gamma", "Delta")]
    html = bs._parlay_section(_ticket(_leg(f"{D}_b", "Gamma", "Delta")), preds)
    assert "⚠ 仅参考" in html and "竞彩未开售" in html
    assert "正EV 推荐" not in html
    assert "1 张仅参考" in html


def test_missing_match_on_board_is_reference(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    preds = [_pred(f"{D}_c", "Eps", "Zeta")]
    html = bs._parlay_section(_ticket(_leg(f"{D}_c", "Eps", "Zeta")), preds)
    assert "未确认在售" in html and "正EV 推荐" not in html


def test_no_snapshot_keeps_old_label_with_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    preds = [_pred("2026-10-23_a", "Alpha", "Beta")]
    html = bs._parlay_section(_ticket(_leg("2026-10-23_a", "Alpha", "Beta")), preds)
    assert "正EV 推荐" in html
    assert "未找到站点赔率快照" in html
