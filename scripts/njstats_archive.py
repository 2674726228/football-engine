#!/usr/bin/env python3
"""NJStats 试用窗口全量归档（2026-10-12，窗口到期前抢核心数据）

背景：试用数据窗口限"最近 3 天"，10-15 00:08 到期后这些数据永久不可得。
本脚本在窗口内把全市场横截面抢回本地：
- match/list 每天全量（含比分 → 结果可自验）
- odds/list 每天全量（多机构指数 → CLV 基准）
- prob 八端点每天全量（他们的概率信号 → 独立 Brier 检验）
不做竞彩过滤——全市场横截面才是统计检验的金矿（几百场样本）。

配额纪律：单次预算 --budget（默认 24 次），与 njstats_capture 的每小时 ~20 次
共享 60/h 限额；进度存 _vault/njstats_archive_progress.json 断点续采。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

API = "https://api.njstats.cn/api/v1"
OUT = ROOT / "data" / "state" / "njstats_archive"
# 进度文件可由环境变量覆盖（CI 上跑时放仓库内以便断点续采跨运行）
PROGRESS = Path(os.environ.get("NJSTATS_ARCHIVE_PROGRESS",
                                str(Path.home() / ".football-engine" / "njstats_archive_progress.json")))
KEY_FILE = Path.home() / ".football-engine" / "njstats_key"
PROXY = {"https": "http://127.0.0.1:7897", "http": "http://127.0.0.1:7897"}

# 窗口: 10-09 起（实测 10-05 已被 1001 拒绝），至今天+2（预置未来日由后续运行填充）
WINDOW_START = "2026-10-09"
PROB_ENDPOINTS = ["draw", "over", "under", "btts", "home", "away", "ht-goal", "avg-goal"]


def load_key() -> str:
    import os
    tok = os.environ.get("NJSTATS_TOKEN", "").strip()
    if tok:
        return tok
    return KEY_FILE.read_text(encoding="utf-8").strip() if KEY_FILE.exists() else ""


def api_get(path: str, key: str, params: dict) -> dict | None:
    p = {"apikey": key, **params}
    try:
        r = requests.get(f"{API}{path}", params=p, proxies=PROXY, timeout=(3.05, 20))
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        d = r.json()
    except ValueError:
        return None
    lim = d.get("limit") or {}
    if isinstance(lim, dict) and lim.get("remaining", 99) < 6:
        print("  ⛔ 额度不足，停止")
        return {"_stop": True}
    if d.get("code") not in (0, None):
        return {"_error": d.get("code")}
    return d


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NJStats 窗口全量归档")
    parser.add_argument("--budget", type=int, default=24, help="本次最多调用数")
    parser.add_argument("--start", default=WINDOW_START)
    parser.add_argument("--days", type=int, default=6, help="窗口天数")
    args = parser.parse_args(argv)

    key = load_key()
    if not key:
        print("njstats-archive: 无 key，跳过")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)

    progress = {}
    if PROGRESS.exists():
        try:
            progress = json.loads(PROGRESS.read_text(encoding="utf-8"))
        except Exception:
            progress = {}
    done = set(progress.get("done", []))

    dates = [(datetime.strptime(args.start, "%Y-%m-%d") + timedelta(days=i)).strftime("%Y-%m-%d")
             for i in range(args.days)]
    # 任务顺序 = 优先级：每天 赛程+赛果 → 指数(含分页，CLV 基准最重) → 概率端点(含分页)
    tasks = []
    for d in dates:
        tasks.append((d, "match", "/match/list", {"date": d, "page": 1, "pageSize": 100}))
        for p in (2, 3):
            tasks.append((d, f"match_p{p}", "/match/list", {"date": d, "page": p, "pageSize": 100}))
        tasks.append((d, "odds", "/odds/list", {"date": d, "page": 1, "pageSize": 100}))
        for p in (2, 3):
            tasks.append((d, f"odds_p{p}", "/odds/list", {"date": d, "page": p, "pageSize": 100}))
    for d in dates:
        for ep in PROB_ENDPOINTS:
            tasks.append((d, f"prob_{ep}", f"/prob/{ep}", {"page": 1, "pageSize": 100}))
            tasks.append((d, f"prob_{ep}_p2", f"/prob/{ep}", {"page": 2, "pageSize": 100}))

    # team 系列（2026-10-12 补测全通）：seasonId/teamId 从当日 match 归档动态取
    for d in dates:
        mfile = OUT / f"{d}_match.json"
        if not mfile.exists():
            continue
        try:
            rows = json.loads(mfile.read_text(encoding="utf-8"))["data"]
            sid = rows[0].get("seasonId")
            tid = rows[0].get("homeId")
        except Exception:
            continue
        if not sid or not tid:
            continue
        tasks.append((d, "team_list", "/team/list", {"seasonId": sid, "pageSize": 100}))
        tasks.append((d, "team_stats", "/team/stats/list",
                      {"seasonId": sid, "teamIds": tid}))
        tasks.append((d, "team_recent", "/team/recent/list",
                      {"seasonId": sid, "teamIds": tid, "recentCount": 5}))

    used = 0
    saved = 0
    for d, name, path, params in tasks:
        if used >= args.budget:
            break
        keyid = f"{d}:{name}"
        if keyid in done:
            continue
        dest = OUT / f"{d}_{name}.json"
        if dest.exists() and dest.stat().st_size > 200:
            done.add(keyid)
            continue
        resp = api_get(path, key, params)
        used += 1
        if not resp or resp.get("_stop"):
            break
        if resp.get("_error") in (1001,):      # 窗口外
            done.add(keyid)                     # 不再重试
            continue
        data = resp.get("data")
        if not data:                           # 没有更多页
            done.add(keyid)
            time.sleep(1)
            continue
        dest.write_text(json.dumps({"fetched_at": datetime.now().strftime("%F %T"),
                                    "date": d, "kind": name, "data": data},
                                   ensure_ascii=False), encoding="utf-8")
        done.add(keyid)
        saved += 1
        time.sleep(1.0)

    progress["done"] = sorted(done)
    progress["updated_at"] = datetime.now().strftime("%F %T")
    PROGRESS.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
    total = len(tasks)
    print(f"归档: 本次调用 {used} 次, 新增 {saved} 个文件 | 进度 {len(done)}/{total}"
          f" ({len(done)/total:.0%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())