#!/usr/bin/env python3
"""周度体检摘要：当前评测产物 vs 上次周检（data/state/weekly_digest.jsonl）。

weekly-deep-check 在六份评测 JSON 生成后调用；--write 把本次指标追加进历史。
只读评测产物，不改任何评测逻辑/文件。历史单独存是因为 upgrade_tracker /
clv_report 每天都会被刷新，git 里的上一版不是「上周」。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "state"
HISTORY = STATE / "weekly_digest.jsonl"

sys.path.insert(0, str(ROOT / "scripts"))
import experiment_pace as ep  # noqa: E402  复用 E8/E10 的冻结分池规则

PRECISION = {
    "v2 Brier": 4,
    "rho (Dixon-Coles)": 4,
    "v2 命中率 (%)": 1,
    "CLV close 均值 (%)": 1,
    "CLV fair 均值 (%)": 1,
    "挑战累计命中率 (%)": 1,
}


def _load(name: str) -> dict:
    try:
        return json.loads((STATE / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _pct(part: int, total: int):
    return round(part / total * 100, 1) if total else "—"


def _rho() -> float | str:
    """config/prediction.json 的 rho——它是结算侧自进化写回的量，
    放进周度趋势正好用来验证「写回有没有真的落到 main」。"""
    try:
        cfg = json.loads((ROOT / "config" / "prediction.json").read_text(encoding="utf-8"))
        v = _num(cfg.get("prediction", {}).get("rho"))
        return round(v, 4) if v is not None else "—"
    except (OSError, json.JSONDecodeError, AttributeError):
        return "—"


def collect() -> tuple[dict, dict]:
    up = _load("upgrade_tracker.json")
    clv = _load("clv_report.json")
    ev = _load("eval_registry.json")
    ec = _load("edge_calibration.json")
    ch = _load("challenge_review.json")
    recs = _load("challenge_state.json").get("records") or []

    v2 = up.get("v2_clean") or {}
    overall = clv.get("overall") or {}
    judges = ev.get("judges") or {}
    panel = ev.get("panel") or {}
    settled = ch.get("settled") or []
    hits = sum(1 for r in settled if r.get("hit") is True)

    def scaled(value, factor: float):
        v = _num(value)
        return round(v * factor, 1) if v is not None else "—"

    metrics = {
        "升级判定": up.get("verdict") or "—",
        "v2 样本": v2.get("n", "—"),
        "v2 Brier": round(v2["brier"], 4) if _num(v2.get("brier")) is not None else "—",
        "v2 命中率 (%)": scaled(v2.get("hit_rate"), 100),
        "rho (Dixon-Coles)": _rho(),
        "CLV 注数": overall.get("n", "—"),
        "CLV close 均值 (%)": scaled(overall.get("clv_close_mean"), 100),
        "CLV fair 均值 (%)": scaled(overall.get("clv_fair_mean"), 100),
        "CLV 判定": overall.get("verdict") or "—",
        "评委在线": f"{panel.get('live', 0)}/{panel.get('total', 0)}",
        "评委样本合计": sum(int(j.get("samples") or 0) for j in judges.values()),
        "正 edge 样本": ec.get("n_positive_edge", "—"),
        "E8 攒注 n": len(ep.pool(recs, "E8")),
        "E10 攒注 n": len(ep.pool(recs, "E10")),
        "挑战累计结算": len(settled),
        "挑战累计命中率 (%)": _pct(hits, len(settled)),
    }
    judge_details = {
        k: {"status": v.get("status") or "—",
            "samples": v.get("samples", "—"),
            "verdict": v.get("verdict") or "—"}
        for k, v in judges.items()
    }
    return metrics, judge_details


def load_history() -> list[dict]:
    if not HISTORY.exists():
        return []
    entries = []
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _fmt(key: str, value) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return f"{value:,.{PRECISION.get(key, 0)}f}"
    return str(value)


def _delta(key: str, prev, cur) -> str:
    if prev is None or prev == "—":
        return "—"
    a, b = _num(prev), _num(cur)
    if a is not None and b is not None:
        p = PRECISION.get(key, 0)
        d = b - a
        if abs(d) < 10 ** (-p) / 2:
            return "—"
        return f"{d:+,.{p}f}"
    return "—" if prev == cur else f"{prev} → {cur}"


def render(metrics: dict, judge_details: dict, prev: dict | None,
           will_write: bool = False) -> str:
    if prev:
        lines = [f"对比基线：{prev.get('date', '—')}", "",
                 "| 指标 | 上次 | 本次 | Δ |", "|---|---|---|---|"]
        prev_metrics = prev.get("metrics") or {}
        for key, cur in metrics.items():
            old = prev_metrics.get(key, "—")
            lines.append(f"| {key} | {_fmt(key, old)} | {_fmt(key, cur)} | "
                         f"{_delta(key, old, cur)} |")
        old_judges = prev.get("judges") or {}
        changed = [f"{k}: {old_judges.get(k, '—')} → {v['verdict']}"
                   for k, v in judge_details.items()
                   if old_judges.get(k) != v["verdict"]]
        if changed:
            lines += ["", "评委判定变化：" + "；".join(changed)]
    else:
        note = "，本次指标已写入历史" if will_write else ""
        lines = [f"首次周检：无历史基线{note}。", "",
                 "| 指标 | 本次 |", "|---|---|"]
        lines += [f"| {k} | {_fmt(k, v)} |" for k, v in metrics.items()]
    if judge_details:
        lines += ["", "评委席：" + "；".join(
            f"{name} {j['status']}({j['samples']}, {j['verdict']})"
            for name, j in judge_details.items())]
    return "\n".join(lines)


def append_history(metrics: dict, judges: dict, today: str) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    entry = {"date": today, "metrics": metrics, "judges": judges}
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    metrics, judge_details = collect()
    history = load_history()
    prev = history[-1] if history else None
    write = "--write" in args
    print(render(metrics, judge_details, prev, will_write=write))
    if write:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        verdicts = {k: v["verdict"] for k, v in judge_details.items()}
        append_history(metrics, verdicts, today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
