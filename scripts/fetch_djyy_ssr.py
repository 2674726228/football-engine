"""从 djyylive.com API 抓取比赛数据（v2 - 2026-07-31 改版后）

DJYY 网站改版，不再用 SSR RSC Flight Data，改为公开 JSON API。
本脚本通过 /api/leagues/fixtures + /api/match/{id}/comparison 抓取：
  - 比赛列表（含队名、联赛、比分）
  - Pinnacle 赔率（胜平负 + BTTS + 大小球）
  - DJYY 模型概率（p_home/p_draw/p_away + 比分矩阵 + 大小球）
  - 角球、半全场等

用法: python scripts/fetch_djyy_ssr.py
输出: data/djyy_matches.json
"""
import json
import os
import random
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
# 空串（CI 未配 secret 时展开为空）必须当未配置：旧写法拿 "" 直接
# rstrip("/") 会用出无 scheme 的 URL，urllib 抛 ValueError，
# 而 main() 里 "No fixtures found, keeping existing file" 会让人
# 误以为"今天真的没赛程"——正是 DJYY 死源两周没人发现的路径之一。
# 口径同 engine/sources/djyy.py:_resolve_djyy_base。
_raw_base = (os.environ.get("DJYY_PROXY") or "").strip()
BASE = (_raw_base if _raw_base and "://" in _raw_base
        else "https://djyylive.com").rstrip("/")
OUTPUT = ROOT / "data" / "djyy_matches.json"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


# 2026-10-22：djyylive 的 Cloudflare 挑战是间歇性的（CI 实测 6 次里 2 次 403），
# 单次请求等于把整份快照押在骰子上。403/429/5xx 属于"过一会儿可能就好"，
# 其余 4xx（404 等）重试无意义，直接放弃。
_RETRYABLE = {403, 425, 429, 500, 502, 503, 504}
_ATTEMPTS = 3


def _headers() -> dict:
    h = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://djyylive.com/",
    }
    key = (os.environ.get("DJYY_KEY") or "").strip()
    if key:
        h["X-DJYY-Key"] = key
    return h


def _fetch_json(url: str, timeout: int = 15, attempts: int = _ATTEMPTS) -> dict | list | None:
    last = "unknown"
    for i in range(attempts):
        req = urllib.request.Request(url, headers=_headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code not in _RETRYABLE:
                break
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if i < attempts - 1:
            time.sleep(0.5 * (2 ** i) + random.uniform(0, 0.25))
    print(f"  ✗ Failed after {i + 1} attempt(s): {last} ({url})", file=sys.stderr)
    return None


def fetch_fixtures(
    date_from: str,
    date_to: str,
    category: str = "tier1+euro+tier2+other+world",
) -> list[dict]:
    """抓取赛程（默认覆盖全部联赛分类，与 engine/sources/djyy.py 的 DJYYSource 一致）

    注意: category 值含 '+'，需用 quote 编码（'+' 在 query string 中表示空格）
    """
    url = (
        f"{BASE}/api/leagues/fixtures"
        f"?date_from={date_from}&date_to={date_to}"
        f"&category={urllib.parse.quote(category)}"
    )
    data = _fetch_json(url)
    if isinstance(data, list):
        return data
    return []


def fetch_comparison(match_id: int) -> dict | None:
    url = f"{BASE}/api/match/{match_id}/comparison"
    return _fetch_json(url, timeout=10)


def enrich_matches(fixtures: list[dict], workers: int | None = None):
    """并发抓每场的 comparison，按输入顺序返回 (matches, enriched, comp_failed)。

    2026-10-23：串行抓 200 场 × 单请求 4~26s，duty 侧 600s 超时稳定触发
    （00:10 实测卡满 10 分钟后放弃，旧快照继续用）。并发数用 6 是保守值，
    给 Cloudflare 留余地；请求失败仍走 _fetch_json 自己的重试。
    """
    if workers is None:
        try:
            workers = max(1, int(os.environ.get("DJYY_FETCH_WORKERS", "6")))
        except ValueError:
            workers = 6
    valid = [fx for fx in fixtures if fx.get("id")]
    matches: list[dict] = []
    enriched = comp_failed = 0

    def _fetch(fx: dict):
        try:
            return fetch_comparison(fx["id"])
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        comps = pool.map(_fetch, valid)
        for i, (fx, comp) in enumerate(zip(valid, comps)):
            if comp is None:
                comp_failed += 1
            m = extract_match(fx, comp)
            matches.append(m)
            if m.get("home_odds_djyy"):
                enriched += 1
            if (i + 1) % 10 == 0:
                print(f"    ... {i+1}/{len(valid)} ({enriched} with odds)")
    return matches, enriched, comp_failed


def _safe(d: dict | None, key: str, default=None):
    """安全取值: d 或 d[key] 为 None 时返回 default"""
    if not isinstance(d, dict):
        return default
    v = d.get(key)
    return v if v is not None else default


def extract_match(fixture: dict, comp: dict | None) -> dict:
    m = {
        "fs_match_id": fixture.get("id"),
        "home_name": _safe(_safe(fixture, "home"), "name_en", ""),
        "away_name": _safe(_safe(fixture, "away"), "name_en", ""),
        "home_name_cn": _safe(_safe(fixture, "home"), "name_zh", ""),
        "away_name_cn": _safe(_safe(fixture, "away"), "name_zh", ""),
        "league": _safe(_safe(fixture, "league"), "name_en", ""),
        "league_zh": _safe(_safe(fixture, "league"), "name_zh", ""),
        "starting_at": fixture.get("starting_at", ""),
        "status": _safe(_safe(fixture, "score"), "status", ""),
        "home_goals": str(_safe(_safe(fixture, "score"), "home", "")),
        "away_goals": str(_safe(_safe(fixture, "score"), "away", "")),
        "has_odds": fixture.get("has_odds", False),
        # Defaults
        "home_odds_djyy": None,
        "draw_odds_djyy": None,
        "away_odds_djyy": None,
        "djyy_model_prob": None,
        "odds_source": None,
        "home_prematch_xg": None,
        "away_prematch_xg": None,
        "btts_yes_odds": None,
        "btts_no_odds": None,
        "over_25_odds": None,
        "under_25_odds": None,
        "top_scores": None,
        "totals": None,
    }

    if not comp:
        return m

    # Model probabilities
    model = comp.get("model") or {}
    if model.get("p_home") is not None:
        m["djyy_model_prob"] = {
            "home": model.get("p_home"),
            "draw": model.get("p_draw"),
            "away": model.get("p_away"),
        }
        m["top_scores"] = model.get("top_scores")
        m["totals"] = model.get("totals")

    # Markets → Pinnacle odds
    markets = comp.get("markets") or []
    for mk in markets:
        key = mk.get("key", "")
        bm = mk.get("bookmaker")
        if not bm:
            continue

        raw = bm.get("raw_odds") or {}

        if key == "1x2_fulltime":
            m["home_odds_djyy"] = raw.get("home")
            m["draw_odds_djyy"] = raw.get("draw")
            m["away_odds_djyy"] = raw.get("away")
            m["odds_source"] = f"DJYY/{bm.get('name', 'Pinnacle')}"

        elif key == "btts":
            m["btts_yes_odds"] = raw.get("yes")
            m["btts_no_odds"] = raw.get("no")

        elif key in ("total_over_under_2_5", "totals_2_5"):
            m["over_25_odds"] = raw.get("over")
            m["under_25_odds"] = raw.get("under")

    return m


def _existing_snapshot() -> dict | None:
    try:
        data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _age_days(extracted_at: str) -> int | None:
    try:
        ts = datetime.fromisoformat(extracted_at)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).days


def main():
    print("[fetch_djyy_v2] Fetching from djyylive.com API...")

    # 抓今明两天（UTC 日期，多抓一天覆盖时区差异）
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_to = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")

    fixtures = fetch_fixtures(today, date_to)
    print(f"  ✓ Fixtures: {len(fixtures)} matches")

    if not fixtures:
        prev = _existing_snapshot()
        if prev:
            at = prev.get("extracted_at") or "未知时间"
            age = _age_days(at)
            print(f"  ⚠ 赛程为空（抓取失败或真无赛程），保留旧快照 {at}"
                  + (f"（{age} 天前，下游将拿到过期 DJYY 数据）" if age is not None else ""))
        else:
            print("  ⚠ 赛程为空且无旧快照，本次不产出")
        return

    matches, enriched, comp_failed = enrich_matches(fixtures)

    # 赛程拿到了但 comparison 全挂（典型的"半程 403"）：这时写盘会把
    # 有效的 djyy_prob/赔率全部抹成 null，比保留旧快照伤害大得多。
    prev = _existing_snapshot() or {}
    if enriched == 0 and comp_failed and (prev.get("with_odds") or 0) > 0:
        print(f"  ⚠ {comp_failed}/{len(fixtures)} 场 comparison 抓取失败，"
              f"保留旧快照（with_odds={prev.get('with_odds')}），不覆盖为全 null")
        return

    output = {
        "source": "djyylive.com API v2",
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "total": len(matches),
        "with_odds": enriched,
        "matches": matches,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"  ✓ {len(matches)} matches ({enriched} with Pinnacle odds) → {OUTPUT}")


if __name__ == "__main__":
    main()
