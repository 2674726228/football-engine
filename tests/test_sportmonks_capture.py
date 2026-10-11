"""sportmonks_capture 采集逻辑单测（2026-10-11）

client 用假对象注入，路径 monkeypatch 到临时目录，不触网。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sportmonks_capture as cap


class FakeClient:
    """可编程的假 SportMonks 客户端。"""
    def __init__(self, pinnacle=None, one_time=None):
        self.pinnacle_ret = pinnacle if pinnacle is not None else {
            "bookmaker_id": 694, "label": "1X2", "value": "1.95"}
        self.one_time = one_time or {}
        self.pinnacle_calls = 0
        self.detail_calls = []

    def pinnacle_1x2(self, fid):
        self.pinnacle_calls += 1
        return self.pinnacle_ret

    def __getattr__(self, name):
        if name in ("predictions", "xg", "lineups", "injuries"):
            def get(fid):
                self.detail_calls.append(name)
                return self.one_time.get(name, [])
            return get
        raise AttributeError(name)


def _seed(tmp_path, day="2026-10-12", n=2):
    preds = [{"match_id": f"{day}_周日{i:03d}", "_djyy_id": 900000 + i}
             for i in range(1, n + 1)]
    (tmp_path / "daily" / day).mkdir(parents=True, exist_ok=True)
    (tmp_path / "daily" / day / "predictions.json").write_text(
        json.dumps(preds), encoding="utf-8")
    (tmp_path / "state" / "sportmonks").mkdir(parents=True, exist_ok=True)
    return preds


def test_capture_appends_odds_and_details(tmp_path, monkeypatch):
    preds = _seed(tmp_path)
    monkeypatch.setattr(cap, "DAILY", tmp_path / "daily")
    monkeypatch.setattr(cap, "OUT_DIR", tmp_path / "state" / "sportmonks")
    fc = FakeClient(one_time={"predictions": [{"id": 1}], "lineups": []})
    day = "2026-10-12"

    s = cap.capture_day(fc, day)
    assert s["fixtures"] == 2 and s["odds_appends"] == 2 and s["one_time"] == 2  # 2场×predictions
    # 赔率追加成 JSONL
    odds_file = tmp_path / "state" / "sportmonks" / f"{day}_900001_odds.jsonl"
    assert len(odds_file.read_text().strip().splitlines()) == 1
    # 空阵容（[]）不算完成，predictions 完成记 manifest
    mf = json.loads((tmp_path / "state" / "sportmonks" / f"{day}_manifest.json").read_text())
    assert mf["900001"]["one_time"]["predictions"] is True
    assert "lineups" not in mf["900001"]["one_time"]


def test_capture_dedupes_one_time_but_keeps_appending_odds(tmp_path, monkeypatch):
    _seed(tmp_path, n=1)
    monkeypatch.setattr(cap, "DAILY", tmp_path / "daily")
    monkeypatch.setattr(cap, "OUT_DIR", tmp_path / "state" / "sportmonks")
    fc = FakeClient(one_time={"predictions": [{"id": 1}], "xg": {"home": 1.2},
                               "lineups": [{"id": 1}], "injuries": [{"id": 1}]})
    day = "2026-10-12"
    cap.capture_day(fc, day)
    calls_after_first = len(fc.detail_calls)
    s2 = cap.capture_day(fc, day)
    assert s2["skipped_done"] == 4                # 四端点全部已完成，跳过
    assert len(fc.detail_calls) == calls_after_first
    assert s2["odds_appends"] == 1                # 赔率仍追加（价格序列）


def test_capture_marks_unavailable_once(tmp_path, monkeypatch):
    _seed(tmp_path, n=1)
    monkeypatch.setattr(cap, "DAILY", tmp_path / "daily")
    monkeypatch.setattr(cap, "OUT_DIR", tmp_path / "state" / "sportmonks")
    fc = FakeClient(pinnacle={"error": "odds_not_entitled", "detail": "403"})
    day = "2026-10-12"
    s = cap.capture_day(fc, day)
    assert s["unavailable"] >= 1
    mf = json.loads((tmp_path / "state" / "sportmonks" / f"{day}_manifest.json").read_text())
    assert mf["900001"]["odds_status"] == "odds_not_entitled"


def test_main_skips_without_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cap, "DAILY", tmp_path / "daily")
    monkeypatch.setattr(cap, "OUT_DIR", tmp_path / "state" / "sportmonks")
    monkeypatch.setattr("engine.sources.sportmonks.load_token", lambda: "")
    rc = cap.main(["--quiet"])
    assert rc == 0
