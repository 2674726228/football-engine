"""周度摘要与上周对比（2026-10-22）：基线取独立历史，不取每日刷新的 JSON。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import weekly_digest as wd  # noqa: E402


def _write(base: Path, name: str, data: dict) -> None:
    (base / name).write_text(json.dumps(data), encoding="utf-8")


def _seed(base: Path, *, brier=0.5511, n=712, hit=0.5772, clv_n=806,
          verdict="IMPROVED", challenge=26, hits=14) -> None:
    _write(base, "upgrade_tracker.json",
           {"verdict": verdict, "v2_clean": {"n": n, "brier": brier, "hit_rate": hit}})
    _write(base, "clv_report.json",
           {"overall": {"n": clv_n, "clv_close_mean": -0.0255,
                        "clv_fair_mean": -0.129, "verdict": "NEGATIVE_CLV"}})
    _write(base, "eval_registry.json",
           {"panel": {"total": 6, "live": 6},
            "judges": {"a": {"samples": 700, "verdict": "OK"},
                       "b": {"samples": 112, "verdict": "HOLD"}}})
    _write(base, "edge_calibration.json", {"n_positive_edge": 116})
    _write(base, "challenge_review.json",
           {"week": "2026-W43",
            "settled": [{"hit": i < hits} for i in range(challenge)]})


def _use_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(wd, "STATE", tmp_path)
    monkeypatch.setattr(wd, "HISTORY", tmp_path / "weekly_digest.jsonl")


def test_first_run_has_no_baseline(tmp_path, monkeypatch, capsys):
    _use_tmp(tmp_path, monkeypatch)
    _seed(tmp_path)
    assert wd.main([]) == 0
    out = capsys.readouterr().out
    assert "首次周检" in out
    assert "0.5511" in out
    assert not wd.HISTORY.exists(), "不带 --write 不应写历史"


def test_diff_against_last_weekly_entry(tmp_path, monkeypatch, capsys):
    _use_tmp(tmp_path, monkeypatch)
    _seed(tmp_path)
    wd.HISTORY.write_text(json.dumps({
        "date": "2026-10-19",
        "metrics": {"升级判定": "DECLINED", "v2 样本": 700,
                    "v2 Brier": 0.5611, "v2 命中率 (%)": 57.0},
        "judges": {"a": "OK", "b": "OK"},
    }) + "\n", encoding="utf-8")
    assert wd.main([]) == 0
    out = capsys.readouterr().out
    assert "2026-10-19" in out
    assert "+12" in out, "样本差应显示 +12"
    assert "-0.0100" in out, "Brier 差应按 4 位精度显示"
    assert "DECLINED → IMPROVED" in out
    assert "b: OK → HOLD" in out, "评委判定变化应单列"


def test_missing_files_and_zero_samples_do_not_crash(tmp_path, monkeypatch, capsys):
    _use_tmp(tmp_path, monkeypatch)
    assert wd.main([]) == 0
    out = capsys.readouterr().out
    assert "挑战累计命中率" in out
    assert "—" in out


def test_write_appends_one_line_per_run(tmp_path, monkeypatch, capsys):
    _use_tmp(tmp_path, monkeypatch)
    _seed(tmp_path)
    assert wd.main(["--write"]) == 0
    assert wd.main(["--write"]) == 0
    lines = wd.HISTORY.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    entries = [json.loads(x) for x in lines]
    assert entries[-1]["metrics"]["v2 样本"] == 712
    assert "对比基线" in capsys.readouterr().out


def test_malformed_history_line_is_ignored(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    wd.HISTORY.write_text('{"date": "2026-10-12", "metrics": {}}\nnot-json\n',
                          encoding="utf-8")
    assert len(wd.load_history()) == 1
