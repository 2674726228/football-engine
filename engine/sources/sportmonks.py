#!/usr/bin/env python3
"""SportMonks Football API v3 客户端（2026-10-11 接入）

定位：数据底座候选——xG 深度、阵容/伤停、50+ 博彩公司赔率（含 Pinnacle，
CLV 基准升级的关键）。DJYY 的后端部分即 SportMonks（context_cache 中已有
其 fixture_id 与 CDN 链接），直连上游 = 官方 schema + 无 Cloudflare 屏蔽。

权益核验（注册后先跑 verify_access()，文档与营销页有冲突项）：
1. 所在套餐/试用是否含 Odds（Standard Odds，含 Pinnacle）——文档称 odds 是付费附加项
2. xG / predictions / lineups / injuries 在当前套餐是否可读
3. 已选联赛清单是否覆盖竞彩常卖 18~30 个联赛

Token 安全：从环境变量 SPORTMONKS_TOKEN 或 ~/.football-engine/sportmonks_token
读取，**绝不入 git**。限流：按套餐 2000~3000 次/h/实体，客户端默认 1.8s 间隔。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

API_BASE = "https://api.sportmonks.com/v3/football"
TOKEN_FILE = Path.home() / ".football-engine" / "sportmonks_token"
DEFAULT_MIN_INTERVAL_S = 1.8   # 2000/h ≈ 1.8s；Pro 档 3000/h 可调低

LAST_ERROR: dict = {}


def load_token() -> str:
    tok = os.environ.get("SPORTMONKS_TOKEN", "").strip()
    if tok:
        return tok
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    return ""


def save_token(token: str) -> Path:
    """把 token 存到用户目录（不入 git）。注册后手动调用一次即可。"""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token.strip(), encoding="utf-8")
    os.chmod(TOKEN_FILE, 0o600)
    return TOKEN_FILE


def _record_error(kind: str, detail: str) -> None:
    LAST_ERROR.clear()
    LAST_ERROR.update({"kind": kind, "detail": detail,
                       "at": time.strftime("%Y-%m-%d %H:%M:%S")})


class SportMonksClient:
    """SportMonks v3 轻客户端：限流 + 重试 + 防御性解析。

    解析层刻意做薄：第一次真实 token 接入时以原始 JSON 为准核对字段名，
    任何与预期不符的字段都在 verify_access() 报告里可见，不静默吞掉。
    """

    PINNACLE_HINTS = ("pinnacle", "pinacle", "平博")

    def __init__(self, token: str | None = None, min_interval_s: float = DEFAULT_MIN_INTERVAL_S):
        self.token = (token or load_token()).strip()
        self.min_interval_s = min_interval_s
        self._last_call = 0.0
        self._bookmakers: list[dict] | None = None
        if not self.token:
            raise ValueError(
                "缺少 SportMonks token：设 SPORTMONKS_TOKEN 环境变量，"
                f"或把 token 写入 {TOKEN_FILE}（save_token() 可代写）")

    # ---------------------------------------------------------- HTTP 层

    def _get(self, path: str, params: dict | None = None,
             include: str | None = None, retries: int = 3) -> dict | None:
        p = {"api_token": self.token}
        if params:
            p.update(params)
        if include:
            p["include"] = include
        for attempt in range(retries):
            wait = self.min_interval_s - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                r = requests.get(f"{API_BASE}{path}", params=p, timeout=(3.05, 15))
            except requests.RequestException as e:
                _record_error("network", f"{type(e).__name__}: {e}")
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 429:   # 限流：退避重试
                _record_error("rate_limited", f"path={path}")
                time.sleep(30 * (attempt + 1))
                continue
            if r.status_code in (401, 403):
                # 权限不足 = 权益边界（如无 Odds 加购），不算网络错误
                _record_error("forbidden", f"path={path} status={r.status_code}")
                return {"_status": r.status_code, "_error": r.text[:300]}
            if r.status_code != 200:
                _record_error("http", f"path={path} status={r.status_code}")
                time.sleep(2 * (attempt + 1))
                continue
            try:
                data = r.json()
            except ValueError:
                _record_error("bad_json", f"path={path}")
                return None
            if isinstance(data, dict) and data.get("error"):
                _record_error("api", str(data.get("error"))[:300])
                return None
            return data
        return None

    # ---------------------------------------------------------- 元数据

    def bookmakers(self) -> list[dict]:
        """/odds/bookmakers：用于动态定位 Pinnacle 的 bookmaker_id（不硬编码）。"""
        if self._bookmakers is None:
            d = self._get("/odds/bookmakers", {"per_page": "200"})
            self._bookmakers = (d or {}).get("data") or []
        return self._bookmakers

    def pinnacle_bookmaker_ids(self) -> list[int]:
        out = []
        for b in self.bookmakers():
            name = str(b.get("name", "")).lower()
            if any(h in name for h in self.PINNACLE_HINTS):
                out.append(b.get("id"))
        return [i for i in out if i is not None]

    def leagues(self) -> list[dict]:
        d = self._get("/leagues", {"per_page": "500"})
        return (d or {}).get("data") or []

    # ---------------------------------------------------------- 比赛与数据

    def fixtures_on(self, day: str) -> list[dict]:
        """/fixtures/date/{YYYY-MM-DD}，带联赛与参赛队。"""
        d = self._get(f"/fixtures/date/{day}",
                      {"per_page": "200"},
                      include="league;participants;scores")
        return (d or {}).get("data") or []

    def fixture_odds(self, fixture_id: int) -> list[dict]:
        """/odds/fixtures/{id}：逐条 {bookmaker_id, market_id, label, value, probability}。"""
        d = self._get(f"/odds/fixtures/{fixture_id}", {"per_page": "500"},
                      include="bookmaker;market")
        return (d or {}).get("data") or []

    def pinnacle_1x2(self, fixture_id: int) -> dict | None:
        """从全部赔率条目中筛 Pinnacle 的胜平负（1X2）最新价。

        直接走 _get 以保留 403 哨兵（权益边界）。市场标签以 label 实测为准
        （'1X2'/'Fulltime Result' 等变体在此防御性匹配）；找不到 Pinnacle/1X2
        时返回带 error 的 dict，供权益核验判读。
        """
        d = self._get(f"/odds/fixtures/{fixture_id}", {"per_page": "500"},
                      include="bookmaker;market")
        if d is None:
            return {"error": "fetch_failed", "detail": dict(LAST_ERROR)}
        if d.get("_status"):
            return {"error": "odds_not_entitled", "detail": d.get("_error")}
        rows = d.get("data") or []
        pin_ids = set(self.pinnacle_bookmaker_ids())
        if not pin_ids:
            return {"error": "pinnacle_bookmaker_not_found"}
        best = None
        for row in rows:
            if row.get("bookmaker_id") not in pin_ids:
                continue
            label = str(row.get("label", "")).upper()
            bk = (row.get("bookmaker") or {})
            if bk.get("name") and "pin" not in bk["name"].lower():
                continue
            if not any(k in label for k in ("1X2", "FULLTIME", "WINNER", "胜平负")):
                continue
            if best is None or str(row.get("updated_at", "")) > str(best.get("updated_at", "")):
                best = row
        return best

    def predictions(self, fixture_id: int) -> list[dict]:
        d = self._get(f"/predictions/fixtures/{fixture_id}")
        return (d or {}).get("data") or []

    def xg(self, fixture_id: int) -> dict | None:
        d = self._get(f"/fixtures/{fixture_id}", include="xgfixture")
        data = (d or {}).get("data") or {}
        xg = data.get("xgfixture")
        return (xg[0] if isinstance(xg, list) and xg else xg) if data else None

    def lineups(self, fixture_id: int) -> list[dict]:
        d = self._get(f"/lineups/fixtures/{fixture_id}", {"per_page": "100"},
                      include="player")
        return (d or {}).get("data") or []

    def injuries(self, fixture_id: int) -> list[dict]:
        d = self._get(f"/injuries/fixtures/{fixture_id}", {"per_page": "100"})
        return (d or {}).get("data") or []

    # ---------------------------------------------------------- 权益探针

    def verify_access(self, probe_fixture_id: int | None = None) -> dict:
        """注册后第一步：核验套餐真实权益（文档与营销页冲突项在此落地）。

        任一 endpoint 返回 403/401 → 该功能不在当前套餐；返回 200 → 可用。
        probe_fixture_id 建议传一个已选联赛内的历史比赛 id（免费档=丹麦超/苏超）。
        """
        out: dict = {"token_ok": False, "checks": {}}

        lg = self._get("/leagues", {"per_page": "5"})
        out["token_ok"] = bool(lg and not lg.get("_status"))
        if not out["token_ok"]:
            out["error"] = LAST_ERROR.get("detail", "token 无效或网络失败")
            return out

        n_leagues = self.leagues()
        out["checks"]["leagues_visible"] = len(n_leagues)

        probes = {"odds": None, "predictions": None, "xg": None,
                  "lineups": None, "injuries": None}
        if probe_fixture_id:
            probes["odds"] = self.pinnacle_1x2(probe_fixture_id)
            probes["predictions"] = self._get(f"/predictions/fixtures/{probe_fixture_id}")
            probes["xg"] = self._get(f"/fixtures/{probe_fixture_id}", include="xgfixture")
            probes["lineups"] = self._get(f"/lineups/fixtures/{probe_fixture_id}",
                                          {"per_page": "5"})
            probes["injuries"] = self._get(f"/injuries/fixtures/{probe_fixture_id}",
                                           {"per_page": "5"})
        for k, v in probes.items():
            # 判定语义：403/401 哨兵=权益不含（UNAVAILABLE）；None=请求失败（unknown）；
            # 200 且空数据=有权限但该场无数据（ok）——三者不可混淆。
            if v is None:
                out["checks"][k] = "unknown（请求失败，见 last_error）"
            elif isinstance(v, dict) and v.get("_status"):
                out["checks"][k] = f"UNAVAILABLE: {str(v.get('_error'))[:120]}"
            elif k == "odds" and isinstance(v, dict) and v.get("error"):
                out["checks"][k] = f"UNAVAILABLE: {v['error']}"
            else:
                out["checks"][k] = "ok"
        out["pinnacle_bookmaker_ids"] = self.pinnacle_bookmaker_ids()
        out["last_error"] = dict(LAST_ERROR)
        return out
