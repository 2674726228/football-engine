"""daily-forecast 必须提交 rho 写回的 config/prediction.json（2026-10-22 修复）。

rho MLE 在 engine/main.py::run_settlement 里写回 config/prediction.json。
Actions 工作区是一次性的：提交步骤若不带这份文件，写回永远到不了 main，
线上 rho 会一直停在最后一次人工提交的值（实测 10-12 起 -0.0748）。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "daily-forecast.yml"


def _commit_step() -> str:
    text = WF.read_text(encoding="utf-8")
    start = text.index("- name: Commit data")
    end = text.find("- name:", start + len("- name: Commit data"))
    return text[start:end if end != -1 else len(text)]


def test_daily_forecast_stages_rho_config():
    assert "config/prediction.json" in _commit_step()


def test_rho_fit_still_writes_config():
    main_py = (ROOT / "engine" / "main.py").read_text(encoding="utf-8")
    assert 'ROOT / "config" / "prediction.json"' in main_py
