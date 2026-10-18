#!/usr/bin/env python3
"""DJYY Value Model 审计（2026-10-12）：用 football-data 的 Pinnacle 收盘
审计 djyydata.com 公开战绩（1,667 注）。数据源 _vault/djyy_value_model.json
（端点见 _vault/recon_djyy.md，已从公开文档移除）。

本脚本只在**本地**可跑：数据与侦察笔记同属 _vault/，被 .gitignore 排除，
云端 CI 上该文件不存在。这是 0e1447d93（2026-10-12）的刻意设计——数据口子
不进公开仓库，免被站点封堵。此前 ci-sync 的 `|| true` 会吞掉 FileNotFoundError，
让人误以为月审在云端正常跑（实际从未跑过）。故显式判定：缺文件→打印一行跳过、
退 0，并在 ci-sync 里把该步骤标注为「本地跑」。

结论（2026-10-12 跑）: 底价口径 ROI +13.57%（复现其回测），按市场收盘 +7.21%
（历史真实存在过价差）；但其自 2026-07-17 实盘 222 注 -8.7% ROI——edge 已衰减。
建议每月在本地重跑监控其 live 恢复情况（免费信号源的可选跟随项）。
"""
import json, re, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FDC = ROOT / "data" / "state" / "fdc" / "parsed.jsonl"
SRC = ROOT / "_vault" / "djyy_value_model.json"

def toks(s):
    s = re.sub(r"\b(fc|sc|if|ac|as|afc|bk|sk|fk|cf)\b", " ", s.lower())
    return {t for t in re.sub(r"[^a-z ]", " ", s).split() if len(t) > 2}

def main():
    if not SRC.exists():
        # _vault/ 不入公开仓库：云端 CI 必然走到这里。显式跳过而非抛栈，
        # 免得又被 `|| true` 吞成「跑了但没输出」的假象。
        print(f"djyy_audit: 跳过——{SRC.relative_to(ROOT)} 不存在（_vault 仅本地，CI 上不参与）")
        return 0
    fdc = defaultdict(list)
    for line in open(FDC):
        r = json.loads(line); fdc[r["date"]].append(r)
    d = json.loads(SRC.read_text())
    sel_map = {"Home value": 0, "Draw value": 1, "Away value": 2}
    at_floor, at_close, stats = [], [], {"total": len(d["records"]), "matched": 0, "skipped": 0}
    for rec in d["records"]:
        parts = [x.strip() for x in rec["m"]["en"].split(" vs ")]
        sel = sel_map.get(rec["o"]["en"])
        if len(parts) != 2 or sel is None:
            stats["skipped"] += 1; continue
        best, best_sc = None, 0.0
        for r in fdc.get(rec["d"], []):
            sc = min(len(toks(parts[0]) & toks(r["home_en"])) / max(1, min(len(toks(parts[0])), len(toks(r["home_en"])))),
                     len(toks(parts[1]) & toks(r["away_en"])) / max(1, min(len(toks(parts[1])), len(toks(r["away_en"])))))
            if sc > best_sc: best, best_sc = r, sc
        if best_sc < 0.5:
            stats["skipped"] += 1; continue
        close = ([best.get("psc_h"), best.get("psc_d"), best.get("psc_a")][sel]
                 or [best.get("avgc_h"), best.get("avgc_d"), best.get("avgc_a")][sel])
        if not close:
            stats["skipped"] += 1; continue
        win = rec["w"] == 1
        at_floor.append((rec["odds"] - 1) if win else -1)
        at_close.append((close - 1) if win else -1)
        stats["matched"] += 1
    summary = {"stats": stats,
        "roi_at_floor": round(sum(at_floor) / len(at_floor), 4),
        "roi_at_close": round(sum(at_close) / len(at_close), 4),
        "matched": stats["matched"],
        "live": d.get("live"), "recent30": d.get("recent30")}
    (ROOT / "data" / "state" / "djyy_audit_latest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    sys.exit(main())
