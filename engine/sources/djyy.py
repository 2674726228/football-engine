from __future__ import annotations
"""DJYY 数据源 - 第三方模型概率 + 多庄家赔率 + xG

来源: https://djyylive.com
托管: Cloudflare Pages（海外IP可访问，解决竞彩WAF问题）
数据: SportMonks + 自有 djyy-elo-model

核心API（无鉴权）:
- /api/leagues/fixtures?date_from=&date_to=&category=  赛程+xG+比分
- /api/match/{id}/comparison  模型概率+多庄家赔率+全市场
- /api/match/{id}/info  教练/伤停/裁判/天气/场地
- /api/match/{id}/team_form?limit=5  近期战绩
- /data/league-matrix.json  33联赛场均数据
"""
import json
import os
import time
from datetime import date, datetime, timedelta
from typing import Optional

import requests

from .base import DataSource, Fixture, MatchResult, OddsSnapshot


# 2026-10-11：DJYY 是第三方服务（djyylive.com，不是本项目自有域名），
# 其 Cloudflare 对 GitHub Actions 的数据中心 IP 返回 403（Bot Fight Mode）。
# 本地/桌面网络 200，因此问题只在 CI 侧。
#
# 与 SPORTTERY_PROXY 同一模式：设 DJYY_PROXY 指向自建 Worker 的代理路径即可绕开；
# 未设置时行为与从前完全一致（直连 djyylive.com），便于灰度与回滚。
DJYY_BASE = os.environ.get("DJYY_PROXY", "https://djyylive.com").rstrip("/")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# 2026-10-11：Cloudflare 对 GitHub Actions 的数据中心 IP 返回 403
# （实测：本地 200 / CI 403，已持续 3.5 周，期间 djyy_prob 长期为 null 而无人察觉）。
# 配套方案是在 djyylive 的 Worker 里校验 X-DJYY-Key；
# 设为空字符串则完全不发该 header，等同于修复前的行为（便于灰度）。
DJYY_KEY = os.environ.get("DJYY_KEY", "").strip()
if DJYY_KEY:
    HEADERS["X-DJYY-Key"] = DJYY_KEY

# 最近一次失败的可观测记录：_get_json 此前把 403 吞成 None，
# 导致"数据源被拦截"与"数据源没数据"完全无法区分。
LAST_ERROR: dict = {}


def _record_error(kind: str, detail: str) -> None:
    LAST_ERROR.clear()
    LAST_ERROR.update({
        "kind": kind,
        "detail": detail,
        "at": datetime.now().isoformat(timespec="seconds"),
        "djyy_key_configured": bool(DJYY_KEY),
    })


class DJYYSource(DataSource):
    """DJYY 数据源

    优势:
    - 海外IP可访问（Cloudflare托管），GitHub Actions可用
    - 多庄家赔率（Pinnacle + bet365 + Unibet）
    - 第三方模型概率（djyy-elo-model）
    - xG 数据
    - 初盘/即时盘对比
    - 联赛场均统计（支持联赛独立参数）
    """

    def __init__(self):
        self._league_matrix: Optional[dict] = None

    @property
    def name(self) -> str:
        return "djyy"

    @property
    def priority(self) -> int:
        return 4  # 体彩(1) > 新浪(2) > 500万(3) > DJYY(4)

    def _get_json(self, path: str, params: dict = None, retries: int = 2) -> Optional[dict]:
        """通用 GET 请求。

        2026-10-11：失败原因必须可区分。此前一律 except → return None，
        于是 Cloudflare 拦截（403）与"当天没数据"在日志里完全一样，
        真实原因是 CI 侧被 403 拦了 3.5 周却无人发现。
        """
        url = f"{DJYY_BASE}{path}"
        last: str = ""
        for attempt in range(retries):
            try:
                resp = requests.get(url, params=params, headers=HEADERS, timeout=(3.05, 8))
                if resp.status_code != 200:
                    kind = ("blocked_by_edge" if resp.status_code in (401, 403)
                            else "http_error")
                    last = f"{kind} HTTP {resp.status_code}"
                    if kind == "blocked_by_edge":
                        hint = ("检查 Cloudflare 是否对 GitHub Actions IP 返回 403；"
                                "若已配 DJYY_KEY，确认仓库 Secret 也已配置")
                        if not DJYY_KEY:
                            hint += "（当前进程未读到 DJYY_KEY 环境变量）"
                        # 直连时代 403 = IP 永久封，重试无意义；经 cf-proxy Worker 后
                        # 403 变为 CF 瞬时挑战（2026-10-12 CI 实测：SSR 同参数分钟级
                        # 成功、引擎却 403，直连/Worker 手工复刻均 200），故退避重试。
                        if attempt < retries - 1:
                            time.sleep(2 * (attempt + 1))
                            continue
                        _record_error(kind, f"{last} {path} — {hint}")
                        print(f"    [djyy] {last} {path} — {hint}")
                        return None
                    if attempt < retries - 1:
                        time.sleep(1)
                        continue
                    _record_error(kind, f"{last} {path}")
                    return None
                LAST_ERROR.clear()
                return resp.json()
            except requests.RequestException as e:
                last = f"network {type(e).__name__}"
                if attempt < retries - 1:
                    time.sleep(1)
                    continue
                _record_error("network", f"{last} {path}: {e}")
                return None
            except json.JSONDecodeError as e:
                last = f"bad_json {type(e).__name__}"
                _record_error("bad_json", f"{last} {path}: {e}")
                return None
        _record_error("unknown", f"{last} {path}")
        return None

    def fetch_fixtures(self, target_date: date) -> list[Fixture]:
        """获取赛程 + xG + 赔率标记

        API: /api/leagues/fixtures?date_from=&date_to=&category=
        """
        date_str = target_date.isoformat()
        # 取前后一天确保覆盖时区差异
        date_from = (target_date - timedelta(days=1)).isoformat()
        date_to = (target_date + timedelta(days=1)).isoformat()

        data = self._get_json("/api/leagues/fixtures", params={
            "date_from": date_from,
            "date_to": date_to,
            "category": "tier1+euro+tier2+other+world",
        })

        if not data or not isinstance(data, list):
            return []

        fixtures = []
        for item in data:
            # 只取目标日期的比赛
            starting_at = item.get("starting_at", "")
            if not starting_at.startswith(date_str):
                # 跨日比赛：北京时间日期可能偏移
                pass  # 保留所有，下游按 kickoff 过滤

            fixture_id = item.get("id", "")
            league_info = item.get("league", {})
            home_info = item.get("home", {})
            away_info = item.get("away", {})
            score = item.get("score")
            xg = item.get("xg", {})
            rank = item.get("rank", {})

            fixture = Fixture(
                match_id=f"{date_str}_{fixture_id}",
                competition=league_info.get("name_zh", "") or league_info.get("name_en", ""),
                home_team=home_info.get("name_zh", "") or home_info.get("name_en", ""),
                away_team=away_info.get("name_zh", "") or away_info.get("name_en", ""),
                kickoff=starting_at,
                home_odds=None,  # 赔率在 comparison API 里
                draw_odds=None,
                away_odds=None,
                source=self.name,
            )

            # 附加数据
            fixture._djyy_id = fixture_id
            fixture._xg_home = xg.get("home") if xg else None
            fixture._xg_away = xg.get("away") if xg else None
            fixture._rank_home = rank.get("home") if rank else None
            fixture._rank_away = rank.get("away") if rank else None
            fixture._has_odds = item.get("has_odds", False)
            fixture._league_id = league_info.get("id")
            fixture._league_category = league_info.get("category", "")

            # 已完场的比赛直接有比分
            if score and score.get("status") == "完场":
                fixture._final_home = score.get("home")
                fixture._final_away = score.get("away")

            fixtures.append(fixture)

        return fixtures

    def fetch_match_comparison(self, djyy_id: int) -> Optional[dict]:
        """获取单场比赛的完整对比数据（模型+赔率+全市场）

        返回:
        - model: {p_home, p_draw, p_away, btts, totals, top_scores}
        - markets: [{key, model, bookmaker{Pinnacle/bet365/Unibet}}]
        - bookmaker: 汇总赔率
        - stakes: 动机/情境
        """
        return self._get_json(f"/api/match/{djyy_id}/comparison")

    def fetch_match_info(self, djyy_id: int) -> Optional[dict]:
        """获取比赛详情（教练、伤停、裁判、天气、场地）"""
        return self._get_json(f"/api/match/{djyy_id}/info")

    def fetch_team_form(self, djyy_id: int, limit: int = 5) -> Optional[dict]:
        """获取双方近期战绩"""
        return self._get_json(f"/api/match/{djyy_id}/team_form", params={
            "limit": limit, "v": 2,
        })

    def fetch_match_live(self, djyy_id: int) -> Optional[dict]:
        """获取比赛直播数据（SSE流）
        
        已完场: 返回最终xG/比分/半场比分
        进行中: 返回实时xG/滚球赔率/模型概率
        """
        try:
            resp = self._client.get(
                f"{self.base_url}/api/match/{djyy_id}/live",
                timeout=12, follow_redirects=True,
            )
            if resp.status_code != 200:
                return None
            # SSE格式: "data: {...}" 行
            for line in resp.text.split("\n"):
                if line.startswith("data: "):
                    return json.loads(line[6:])
            return None
        except Exception:
            return None

    def fetch_match_events(self, djyy_id: int) -> Optional[dict]:
        """获取比赛事件（进球/红黄牌/换人/乌龙球）
        
        返回: {fixture_id, available, events: [{minute, type, side, player_name_zh, ...}]}
        """
        return self._get_json(f"/api/match/{djyy_id}/events")

    def fetch_match_lineups(self, djyy_id: int) -> Optional[dict]:
        """获取首发+替补（含球员rating/个人xG/xGOT）
        
        返回: {fixture_id, available, home: {starting: [{name_zh, position, rating, xg, xgot, stats}], bench: [...]}, away: {...}}
        """
        return self._get_json(f"/api/match/{djyy_id}/lineups")

    def fetch_post_match_xg(self, djyy_id: int) -> Optional[dict]:
        """便捷方法: 从live端点提取已完场比赛的真实xG
        
        返回: {home_xg, away_xg, ht_home, ht_away, score_home, score_away} 或 None
        """
        live = self.fetch_match_live(djyy_id)
        if not live or live.get("state") not in ("ft", "aet", "pen"):
            return None
        xg = live.get("xg") or {}
        score = live.get("score") or {}
        ht = live.get("ht_score") or {}
        return {
            "home_xg": xg.get("home"),
            "away_xg": xg.get("away"),
            "score_home": score.get("home"),
            "score_away": score.get("away"),
            "ht_home": ht.get("home"),
            "ht_away": ht.get("away"),
        }

    def fetch_results(self, target_date: date) -> list[MatchResult]:
        """从赛程API提取已完场比赛的结果"""
        fixtures = self.fetch_fixtures(target_date)
        results = []
        for f in fixtures:
            home_score = getattr(f, "_final_home", None)
            away_score = getattr(f, "_final_away", None)
            if home_score is not None and away_score is not None:
                results.append(MatchResult(
                    match_id=f.match_id,
                    home_score=int(home_score),
                    away_score=int(away_score),
                    home_team=f.home_team,
                    away_team=f.away_team,
                    competition=f.competition,
                    match_date=target_date.isoformat(),
                ))
        return results

    def fetch_odds_snapshot(self, target_date: date) -> list[OddsSnapshot]:
        """获取赔率快照（从 comparison 的 markets 取 Pinnacle **真实**赔率）。

        【2026-10-13 修正：原实现用 1/p 转的是公平赔率，不是真实赔率】

        原实现取 `bookmaker["1x2"]`（这是**去水概率**），再 `1/p` 转回赔率。
        实测对比同一场（19713912）：

            markets 里 Pinnacle 真实赔率:  2.43 / 3.72 / 2.82
            原实现 1/p 得到的:            2.515 / 3.85 / 2.919   ← 偏高 3~4%

        偏高的量正好是抽水。也就是说原实现产出的是"公平赔率"，
        却被当成含抽水的真实赔率存进 OddsSnapshot——**与 sporttery
        的口径不一致**（后者直接用 `f.home_odds` 真实价）。
        两处口径不同正是项目反复警告的失真来源：拿它们算 EV 会系统性偏乐观。

        修法：改用 `markets[].bookmaker.raw_odds`（真实含抽水赔率），
        与 sporttery 口径对齐。
        """
        fixtures = self.fetch_fixtures(target_date)
        now = datetime.now().isoformat()
        snapshots = []

        for f in fixtures:
            djyy_id = getattr(f, "_djyy_id", None)
            if not djyy_id or not getattr(f, "_has_odds", False):
                continue

            comparison = self.fetch_match_comparison(djyy_id)
            if not comparison:
                continue

            # 从 markets 里取 1x2_fulltime 的真实赔率（含抽水）
            raw = None
            book_name = None
            for market in (comparison.get("markets") or []):
                if market.get("key") != "1x2_fulltime":
                    continue
                bm = market.get("bookmaker") or {}
                raw = bm.get("raw_odds") or {}
                book_name = bm.get("name")
                if raw:
                    break
            if not raw:
                continue

            def _f(v):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            h, d, a = _f(raw.get("home")), _f(raw.get("draw")), _f(raw.get("away"))
            if not (h and d and a) or min(h, d, a) <= 1.0:
                continue

            snapshots.append(OddsSnapshot(
                match_id=f.match_id,
                timestamp=now,
                home_odds=round(h, 3),
                draw_odds=round(d, 3),
                away_odds=round(a, 3),
                source=f"{self.name}_{(book_name or 'unknown').lower()}",
            ))

            time.sleep(0.2)  # 礼貌延迟

        return snapshots

    def fetch_league_matrix(self) -> dict:
        """获取33联赛场均数据（支持联赛独立参数）

        返回: {league_id: {avg_goals, avg_corners, ...}}
        """
        if self._league_matrix is not None:
            return self._league_matrix

        data = self._get_json("/data/league-matrix.json")
        self._league_matrix = data if data else {}
        return self._league_matrix

    def get_model_probabilities(self, djyy_id: int) -> Optional[dict]:
        """获取 DJYY 模型概率（作为 ensemble 第四路信号）

        Returns:
            {"p_home": float, "p_draw": float, "p_away": float,
             "btts_yes": float, "over25": float, "top_scores": [...]}

        【2026-10-13 标注：已被取代，保留仅为兼容】
        `engine/sources/manager.py` 的富化路径直接读
        `comparison["model"]["p_home"/"p_draw"/"p_away"]`，不走本方法——
        即本方法**从未被调用**，且与现用路径数据源完全相同（都是 comparison）。
        保留它只是为了不破坏可能的外部调用；新代码应该用 `fetch_match_comparison`
        自己取 `model`，避免多一层无谓的封装。

        另外实测：返回的 `btts_yes` / `over25` / `top_scores` **恒为 null/[]**
        （登录与非登录都一样，API 不提供，见 docs/RETROSPECTIVE_20261013_NIGHT.md）。
        所以不要指望这三个字段有值——要 BTTS/大小球信号应走
        `markets` 里 `btts` / `totals_main` 的赔率（含开盘价）。
        """
        comparison = self.fetch_match_comparison(djyy_id)
        if not comparison:
            return None

        model = comparison.get("model", {})
        if not model:
            return None

        totals = model.get("totals", {})
        over25_pair = totals.get("2.5", [None, None])

        return {
            "p_home": model.get("p_home"),
            "p_draw": model.get("p_draw"),
            "p_away": model.get("p_away"),
            "btts_yes": model.get("btts", {}).get("yes"),
            "over25": over25_pair[1] if len(over25_pair) > 1 else None,
            "top_scores": model.get("top_scores", []),
            "source": model.get("source", "djyy-elo-model"),
            "as_of": model.get("as_of", ""),
        }

    def get_opening_vs_current(self, djyy_id: int) -> Optional[dict]:
        """获取初盘 vs 即时盘对比（赔率变动方向分析）

        Returns:
            {"opening": {h, d, a}, "current": {h, d, a}, "movement": {h, d, a}}
        """
        comparison = self.fetch_match_comparison(djyy_id)
        if not comparison:
            return None

        markets = comparison.get("markets", [])
        for market in markets:
            if market.get("key") == "1x2_fulltime":
                bm = market.get("bookmaker", {})
                if not bm:
                    return None
                current = bm.get("raw_odds", {})
                opening = bm.get("opening_raw_odds", {})
                if current and opening:
                    return {
                        "opening": opening,
                        "current": current,
                        "movement": {
                            "home": self._safe_float(current.get("home", 0)) - self._safe_float(opening.get("home", 0) or 0),
                            "draw": self._safe_float(current.get("draw", 0)) - self._safe_float(opening.get("draw", 0) or 0),
                            "away": self._safe_float(current.get("away", 0)) - self._safe_float(opening.get("away", 0) or 0),
                        },
                        "bookmaker": bm.get("name", "Pinnacle"),
                    }
        return None

    def health_check(self) -> bool:
        """检查 DJYY 是否可达"""
        try:
            resp = requests.get(f"{DJYY_BASE}/api/league/directory",
                                headers=HEADERS, timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        try:
            return float(val) if val else None
        except (ValueError, TypeError):
            return None
