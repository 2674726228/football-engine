#!/usr/bin/env python3
"""NJStats 试用层每日收割（2026-10-12）

试用: 至 2026-10-15 00:08，60 次/h，数据限最近 3 天，权益=Pro 同级。
设计（每小时随 auto_run 跑，~12 次调用，远低于 60/h）:
- /match/list 今天+明天 → 与我们的竞彩预测模糊 join → 拿他们的 matchId
- /odds/list 全部分页 → 过滤出我们场次 → 追加指数历史
- /prob/draw 全部分页 → 过滤 → 平局概率+初盘+球队状态特征（每场一份快照）
- 无 key 静默退出；429/limit 不足时收缩（读响应里的 limit.remaining）

产出: data/state/njstats/{date}_join.json（我们场次 enriched 快照）
      data/state/njstats/{date}_odds.jsonl（指数序列）
关键事实: NJStats 的 date 桶比竞彩售球日 +1（竞彩"周一"场开球在周二凌晨），
所以要取今天+明天两天。队名 join 用双向包含模糊匹配（阈值 0.6）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.beijing_time import beijing_today  # noqa: E402

API = "https://api.njstats.cn/api/v1"
OUT = ROOT / "data" / "state" / "njstats"
DAILY = ROOT / "data" / "daily"
KEY_FILE = Path.home() / ".football-engine" / "njstats_key"

# 代理改为可选探测（2026-10-13）：原实现写死 127.0.0.1:7897，
# 不开 Clash 的机器上每次请求静默失败，脚本看起来"跑过了"实际没采到。
# 与 njstats_archive.py 同一处理：环境变量 > 端口探测 > 直连。
def _resolve_proxy() -> dict | None:
    import os
    import socket
    env = os.environ.get("NJSTATS_PROXY", "").strip()
    if env:
        return {"https": env, "http": env}
    for port in (7897, 7890, 1080):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return {"https": f"http://127.0.0.1:{port}", "http": f"http://127.0.0.1:{port}"}
        except OSError:
            continue
    return None

PROXY = _resolve_proxy()
MIN_REMAINING = 6          # 低于此额度本轮收手
SIM_THRESHOLD = 0.6


def load_key() -> str:
    """按 环境变量 > 多个候选文件名 取 key（与 njstats_archive.load_key 同口径）。

    同一个 key 在两处用了不同文件名（njstats_key vs njstats_apikey.txt），
    只认其一会导致静默"无 key 跳过"。
    """
    import os
    tok = os.environ.get("NJSTATS_TOKEN", "").strip()
    if tok:
        return tok
    for name in ("njstats_key", "njstats_apikey.txt", "njstats_token.txt"):
        p = Path.home() / ".football-engine" / name
        if p.exists():
            v = p.read_text(encoding="utf-8").strip()
            if v:
                return v
    return ""


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def api_get(path: str, key: str, params: dict | None = None) -> dict | None:
    p = {"apikey": key}
    if params:
        p.update(params)
    try:
        r = requests.get(f"{API}{path}", params=p, proxies=PROXY, timeout=(3.05, 20))
    except requests.RequestException as e:
        print(f"  ⚠ {path}: {type(e).__name__}")
        return None
    if r.status_code != 200:
        print(f"  ⚠ {path}: HTTP {r.status_code}")
        return None
    try:
        d = r.json()
    except ValueError:
        return None
    lim = d.get("limit") or {}
    if isinstance(lim, dict) and lim.get("remaining", 99) < MIN_REMAINING:
        print(f"  ⛔ 额度不足 remaining={lim.get('remaining')}，本轮停止调用")
        return {"_stop": True}
    return d


def _norm(s: str) -> str:
    return (s or "").replace("队", "").replace(" ", "")


def _sim(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    ca, cb = set(a), set(b)
    return len(ca & cb) / max(1, min(len(ca), len(cb)))


def our_fixtures(day: str) -> list[dict]:
    pf = DAILY / day / "predictions.json"
    if not pf.exists():
        return []
    try:
        d = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return []
    preds = d.get("predictions", d) if isinstance(d, dict) else d
    return preds if isinstance(preds, list) else []


def match_theirs(key: str, days: list[str], budget: dict) -> list[dict]:
    """两天的 match/list → 与我们的竞彩预测 join。消耗 1~2 次调用。"""
    theirs = []
    for day in days:
        if budget["used"] >= budget["max"]:
            break
        d = api_get("/match/list", key, {"date": day, "page": 1, "pageSize": 100})
        budget["used"] += 1
        if not d or d.get("_stop"):
            break
        rows = d.get("data") or []
        theirs += rows if isinstance(rows, list) else []
        # 翻页（pageSize=100 一般一页够，防御性再取一页）
        pager = d.get("pager") or {}
        if isinstance(pager, dict) and (pager.get("maxPage") or 1) >= 2 and budget["used"] < budget["max"]:
            d2 = api_get("/match/list", key, {"date": day, "page": 2, "pageSize": 100})
            budget["used"] += 1
            if d2 and not d2.get("_stop"):
                rows2 = d2.get("data") or []
                theirs += rows2 if isinstance(rows2, list) else []

    joined = []
    for day in days:
        for p in our_fixtures(day):
            best, bs = None, 0.0
            for r in theirs:
                sc = min(_sim(r.get("homeName"), p.get("home_team", "")),
                         _sim(r.get("awayName"), p.get("away_team", "")))
                if sc > bs:
                    best, bs = r, sc
            if bs >= SIM_THRESHOLD:
                joined.append({
                    "match_id": p.get("match_id"), "sim": round(bs, 2),
                    "nj_match_id": best.get("matchId"),
                    "league": best.get("leagueName"),
                    "home": best.get("homeName"), "away": best.get("awayName"),
                    "kickoff": best.get("matchTimeStr"),
                })
    return joined


def sweep_paged(key: str, path: str, budget: dict, max_pages: int = 10) -> list[dict]:
    rows, page = [], 1
    while page <= max_pages and budget["used"] < budget["max"]:
        d = api_get(path, key, {"page": page, "pageSize": 50})
        budget["used"] += 1
        if not d or d.get("_stop"):
            break
        data = d.get("data") or []
        rows += data if isinstance(data, list) else []
        pager = d.get("pager") or {}
        if page >= (pager.get("maxPage") or 1):
            break
        page += 1
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NJStats 试用层收割")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    key = load_key()
    if not key:
        if not args.quiet:
            print("njstats: 未配置 key，跳过")
        return 0

    today = beijing_today()
    tomorrow = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    days = [today, tomorrow]
    budget = {"used": 0, "max": 40}

    OUT.mkdir(parents=True, exist_ok=True)
    joined = match_theirs(key, days, budget)
    our_ids = {j["nj_match_id"] for j in joined}

    # odds 序列（追加）
    odds_rows = sweep_paged(key, "/odds/list", budget, max_pages=4)
    ours_odds = [r for r in odds_rows if str(r.get("matchId")) in our_ids]
    if ours_odds:
        with open(OUT / f"{today}_odds.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _ts(), "rows": ours_odds}, ensure_ascii=False) + "\n")

    # 概率端点轮换（60/h 限额下每小时深扫一个：draw/over/btts 轮流）
    prob_kind = ("draw", "over", "btts")[datetime.now().hour % 3]
    prob_rows = sweep_paged(key, f"/prob/{prob_kind}", budget, max_pages=20)
    ours_prob = [r for r in prob_rows if str(r.get("matchId")) in our_ids]
    for r in ours_prob:
        r["_captured_at"] = _ts()
    (OUT / f"{today}_prob_{prob_kind}.json").write_text(
        json.dumps({"captured_at": _ts(), "kind": prob_kind, "rows": ours_prob},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    ours_draw = ours_prob

    snapshot = {
        "generated_at": _ts(), "day": today,
        "joined_n": len(joined), "calls_used": budget["used"],
        "join": joined,
        f"prob_{prob_kind}": len(ours_prob),
    }
    (OUT / f"{today}_join.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")

    if not args.quiet:
        print(f"njstats 收割[{today}]: join {len(joined)}/{sum(len(our_fixtures(d)) for d in days)} 场, "
              f"odds {len(ours_odds)}, prob_draw {len(ours_draw)}, 调用 {budget['used']} 次")
    return 0


if __name__ == "__main__":
    sys.exit(main())
