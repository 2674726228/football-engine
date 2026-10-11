#!/usr/bin/env python3
"""SportMonks 每日采集（2026-10-11，免费档/试用通用）

行为：
- 无 token → 静默退出（挂进 auto_run 无副作用）；有 token → 按当日预测采集
- 关键事实：djyy_id == SportMonks fixture_id（33/33 恒等实测，DJYY 透传上游 ID），
  因此预测记录的 _djyy_id 可直接当 fixture_id 用
- 赔率：每次运行都拉并追加（形成价格序列，同 odds_series 模式），含 Pinnacle 1X2
- predictions/xg/lineups/injuries：每场每日拉一次；空结果（如开赛前阵容未出）次日重试
- 403/权益边界：记入 manifest，不中断（免费档只有丹麦超/苏超）

产出：data/state/sportmonks/{date}_{fixture_id}_odds.jsonl（价格序列）
      data/state/sportmonks/{date}_{fixture_id}_details.json（一次性数据）
      data/state/sportmonks/{date}_manifest.json（采集状态）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.beijing_time import beijing_today  # noqa: E402

DAILY = ROOT / "data" / "daily"
OUT_DIR = ROOT / "data" / "state" / "sportmonks"


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_preds(day: str) -> list[dict]:
    pf = DAILY / day / "predictions.json"
    if not pf.exists():
        return []
    try:
        d = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return []
    return d.get("predictions", d) if isinstance(d, dict) else d


def capture_day(client, day: str) -> dict:
    preds = _load_preds(day)
    targets = [(p.get("match_id"), p.get("_djyy_id"))
               for p in preds if p.get("_djyy_id")]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mf_path = OUT_DIR / f"{day}_manifest.json"
    manifest = {}
    if mf_path.exists():
        try:
            manifest = json.loads(mf_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

    ONE_TIME = ("predictions", "xg", "lineups", "injuries")
    stats = {"fixtures": len(targets), "odds_appends": 0, "one_time": 0,
             "skipped_done": 0, "errors": 0, "unavailable": 0}

    for match_id, fid in targets:
        fid = int(fid)
        m = manifest.setdefault(str(fid), {"match_id": match_id})
        one = m.setdefault("one_time", {})

        # 赔率：每次运行追加（价格序列）
        try:
            pin = client.pinnacle_1x2(fid)
        except Exception as e:
            pin = {"error": f"{type(e).__name__}: {e}"}
        if isinstance(pin, dict) and pin.get("error"):
            if "not_entitled" in str(pin.get("error")) or "not_found" in str(pin.get("error")):
                stats["unavailable"] += 1
                m["odds_status"] = pin["error"]
        else:
            row = {"ts": _ts(), "pinnacle_1x2": pin}
            with open(OUT_DIR / f"{day}_{fid}_odds.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            stats["odds_appends"] += 1
            m["odds_status"] = "ok"

        # 一次性数据：成功后不再拉；空/失败次日重试
        details_path = OUT_DIR / f"{day}_{fid}_details.json"
        details = {}
        if details_path.exists():
            try:
                details = json.loads(details_path.read_text(encoding="utf-8"))
            except Exception:
                details = {}
        for key in ONE_TIME:
            if one.get(key):
                stats["skipped_done"] += 1
                continue
            try:
                getter = getattr(client, key)
                data = getter(fid)
            except Exception as e:
                data = {"_error": f"{type(e).__name__}: {e}"}
            if data is None:
                continue
            if isinstance(data, dict) and data.get("_status"):
                one[key] = "unavailable"
                stats["unavailable"] += 1
                continue
            if isinstance(data, dict) and data.get("_error"):
                continue                      # 瞬时错误，下次重试
            if isinstance(data, list) and not data:
                continue                      # 空数据（如阵容未出），下次重试
            details[key] = {"fetched_at": _ts(), "data": data}
            one[key] = True
            stats["one_time"] += 1
        details_path.write_text(json.dumps(details, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        m["updated_at"] = _ts()

        time.sleep(0.2)   # 端点间微间隔，叠加客户端自身限流

    mf_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    summary_path = OUT_DIR / f"{day}_summary.json"
    summary = {"generated_at": _ts(), **stats}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SportMonks 每日采集")
    parser.add_argument("--date", default=beijing_today(), help="目标日期（默认今天）")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    from engine.sources.sportmonks import SportMonksClient, load_token
    if not load_token():
        if not args.quiet:
            print("sportmonks: 未配置 token，跳过采集")
        return 0

    client = SportMonksClient()
    summary = capture_day(client, args.date)
    if not args.quiet:
        print(f"sportmonks 采集[{args.date}]: {json.dumps(summary, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
