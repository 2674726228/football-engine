"""
同赔分析 — 历史相同/相似赔率下的赛果统计。

来源: football_frontend 同赔匹配
核心思想:
  - 给定当前赔率组合 (主胜/平/客胜)，在历史数据中找相似赔率
  - 统计这些"同赔"比赛的实际赛果分布
  - 如果历史上同赔比赛主胜率70%，但当前赔率隐含主胜率只有55%，
    则说明市场可能低估了主队
  - 容差: ±0.015 (欧赔精度通常到小数点后2位)
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SameOddsConfig:
    """同赔分析参数

    ## 2026-10-15 实测：这条链路对最终概率的影响 <0.7pp，且 84% 的场次
    ## 连门槛都过不了。参数全部保留（消融回放要能翻开关），但**不要
    ## 以为调 tolerance 能救活它**——量化见下。

    ### 为什么实际扰动这么小（全链路 1065 场实测）

    传导链：bias → ×(max_adjust 0.05 × confidence) → 概率扰动

      - confidence 中位数 0.0，均值 0.027，**最大 0.2592**
        （公式 min(1, n/50)×(1-1.96/(2√n))，n=20 才 0.31、
         n=5 只有 0.056；而 tolerance=0.015 下匹配数中位只有 3）
      - 门槛 same_odds_min_confidence=0.3 → **1065 场中 0 场通过**
        （有匹配 602 场 = 56.5%，但 confidence 全 < 0.3）
      - 即使降到 0，扰动 = 0.05 × 0.2592 × 0.55(bias 极值) ≈ 0.0072

    ### 放大 tolerance 的代价（实测）

      tol=0.015 → 中位匹配 3，过闸 0 场
      tol=0.030 → 中位匹配 7，过闸 96 场
      tol=0.050 → 中位匹配 23，过闸 493 场
      tol=0.080 → 中位匹配 45，过闸 627 场

    能过闸，但那时"同赔"已经名不副实：容差 5% 意味着一场 1.80 和
    一场 1.89 被算作"同一赔率"，而 bias 的 sd 高达 0.16（极值
    ±0.55）——那是赔率结构差异带来的噪声，不是历史同形的信号。

    ### 结论

    `same_odds_bias` 是 fusion 链上唯一开着的开关
    （config/prediction.json post_fusion 其余 9 个全 false），
    但 ablation 实证 delta_brier=0.0、t=0.0、n=426 → 中性。
    保留它不是因为它有用，而是因为**关掉它要动 fusion 链**
    （AGENTS.md：每一步都是配置开关，keep/kill 只由消融回放裁决）。
    在拿到能推翻 426 场零效应的证据之前，维持现状。
    """
    tolerance: float = 0.015       # 赔率匹配容差（见上方 docstring：放大即失真）
    min_matches: int = 5           # 最少匹配场次才出结论
    max_matches: int = 200         # 最多使用匹配数
    weight_recent: bool = True     # 是否对近期比赛加权（未接线，见下）
    recent_half_life_days: int = 365  # 时间衰减半衰期（未接线，见下）

    # weight_recent / recent_half_life_days 从未被读取：analyze() 里只按
    # date 倒序截断 max_matches，没有任何按半衰期加权的代码。留着是因为
    # tests/test_same_odds.py 会构造 SameOddsConfig，删字段会破坏构造签名。


@dataclass
class OddsRecord:
    """历史赔率记录"""
    match_id: str
    date: str
    home_odds: float
    draw_odds: float
    away_odds: float
    result: str  # "H" / "D" / "A"
    league: str = ""


@dataclass
class SameOddsResult:
    """同赔分析结果"""
    matched_count: int = 0
    home_win_rate: float = 0.0
    draw_rate: float = 0.0
    away_win_rate: float = 0.0
    # 与当前赔率隐含概率的偏差
    home_bias: float = 0.0    # 正=历史支持主胜多于赔率暗示
    draw_bias: float = 0.0
    away_bias: float = 0.0
    confidence: float = 0.0   # 基于样本量的置信度
    matches: list[dict] = field(default_factory=list)


class SameOddsAnalyzer:
    """
    同赔分析器。

    用法:
        analyzer = SameOddsAnalyzer("data/historical/odds.csv")
        result = analyzer.analyze(home_odds=1.85, draw_odds=3.40, away_odds=4.20)
        # result.home_win_rate = 历史同赔主胜率
        # result.home_bias = 历史主胜率 - 赔率隐含主胜率
    """

    def __init__(
        self,
        odds_path: str | Path = "data/historical/odds.csv",
        config: SameOddsConfig | None = None,
    ):
        self.cfg = config or SameOddsConfig()
        self.records: list[OddsRecord] = []
        self._load(Path(odds_path))

    def _load(self, path: Path) -> None:
        """加载历史赔率数据。

        【2026-10-13 修正：原实现按不存在的列名读取，永远加载 0 条】

        `data/historical/odds.csv` 的实际列名是：

            date, competition, home_team, away_team, home_score,
            away_score, handicap, home_odds, draw_odds, away_odds, ...

        而原实现读的是 `match_id` / `result` / `league`——**三个都不存在**，
        于是 `result` 恒为空串 → `result in ("H","D","A")` 恒 False →
        **加载 0 条记录**。后果是 `analyze()` 永远 matched_count=0，
        `find_value_signals()` 永远返回空列表——**看起来"正常工作"，
        实际这个功能从没真正跑通过**（这也是它一直没被发现的原因：
        不报错，只是没信号）。

        修法：按真实列名读取，赛果由 `home_score`/`away_score` 推导：
          主 > 客 → "H"；相等 → "D"；主 < 客 → "A"
        """
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    hs, as_ = row.get("home_score"), row.get("away_score")
                    result = ""
                    try:
                        hs_i, as_i = int(hs), int(as_)
                        result = "H" if hs_i > as_i else ("D" if hs_i == as_i else "A")
                    except (TypeError, ValueError):
                        result = ""
                    rec = OddsRecord(
                        match_id=(
                            row.get("match_id")
                            or f"{row.get('date','')}_{row.get('home_team','')}_vs_{row.get('away_team','')}"
                        ),
                        date=row.get("date", ""),
                        home_odds=float(row.get("home_odds", 0)),
                        draw_odds=float(row.get("draw_odds", 0)),
                        away_odds=float(row.get("away_odds", 0)),
                        result=result,
                        league=row.get("competition", ""),
                    )
                    if rec.home_odds > 0 and rec.result in ("H", "D", "A"):
                        self.records.append(rec)
                except (ValueError, TypeError):
                    continue

    def analyze(
        self,
        home_odds: float,
        draw_odds: float,
        away_odds: float,
        league: str | None = None,
    ) -> SameOddsResult:
        """
        查找历史同赔比赛并统计赛果。

        Args:
            home_odds: 当前主胜赔率
            draw_odds: 当前平局赔率
            away_odds: 当前客胜赔率
            league: 可选，限定联赛
        """
        tol = self.cfg.tolerance
        matched = []

        for rec in self.records:
            if league and rec.league != league:
                continue
            if (
                abs(rec.home_odds - home_odds) <= tol
                and abs(rec.draw_odds - draw_odds) <= tol
                and abs(rec.away_odds - away_odds) <= tol
            ):
                matched.append(rec)

        # 限制最大匹配数（优先近期）
        if len(matched) > self.cfg.max_matches:
            matched.sort(key=lambda r: r.date, reverse=True)
            matched = matched[: self.cfg.max_matches]

        if len(matched) < self.cfg.min_matches:
            return SameOddsResult(matched_count=len(matched))

        # 统计赛果
        h_count = sum(1 for r in matched if r.result == "H")
        d_count = sum(1 for r in matched if r.result == "D")
        a_count = sum(1 for r in matched if r.result == "A")
        total = len(matched)

        h_rate = h_count / total
        d_rate = d_count / total
        a_rate = a_count / total

        # 赔率隐含概率（去水后）
        raw_sum = 1 / home_odds + 1 / draw_odds + 1 / away_odds
        implied_h = (1 / home_odds) / raw_sum
        implied_d = (1 / draw_odds) / raw_sum
        implied_a = (1 / away_odds) / raw_sum

        # 偏差 = 历史实际 - 赔率隐含
        home_bias = h_rate - implied_h
        draw_bias = d_rate - implied_d
        away_bias = a_rate - implied_a

        # 置信度（基于样本量，Wilson思想简化版）
        confidence = min(1.0, total / 50) * (1 - 1.96 / (2 * total**0.5))
        confidence = max(0.0, confidence)

        return SameOddsResult(
            matched_count=total,
            home_win_rate=round(h_rate, 4),
            draw_rate=round(d_rate, 4),
            away_win_rate=round(a_rate, 4),
            home_bias=round(home_bias, 4),
            draw_bias=round(draw_bias, 4),
            away_bias=round(away_bias, 4),
            confidence=round(confidence, 4),
            matches=[
                {"id": r.match_id, "date": r.date, "result": r.result, "league": r.league}
                for r in matched[:20]  # 只返回前20条详情
            ],
        )

    def find_value_signals(
        self,
        home_odds: float,
        draw_odds: float,
        away_odds: float,
        threshold: float = 0.05,
    ) -> list[dict]:
        """
        找出正偏差信号（历史实际概率 > 赔率隐含概率超过阈值）。
        """
        result = self.analyze(home_odds, draw_odds, away_odds)
        signals = []

        if result.matched_count < self.cfg.min_matches:
            return signals

        if result.home_bias > threshold:
            signals.append({
                "selection": "home",
                "bias": result.home_bias,
                "historical_rate": result.home_win_rate,
                "implied_prob": round(1 / home_odds, 4),
            })
        if result.draw_bias > threshold:
            signals.append({
                "selection": "draw",
                "bias": result.draw_bias,
                "historical_rate": result.draw_rate,
                "implied_prob": round(1 / draw_odds, 4),
            })
        if result.away_bias > threshold:
            signals.append({
                "selection": "away",
                "bias": result.away_bias,
                "historical_rate": result.away_win_rate,
                "implied_prob": round(1 / away_odds, 4),
            })

        return signals

    def stats_summary(self) -> dict:
        """数据摘要"""
        return {
            "total_records": len(self.records),
            "leagues": len(set(r.league for r in self.records if r.league)),
            "date_range": (
                min(r.date for r in self.records) if self.records else "",
                max(r.date for r in self.records) if self.records else "",
            ),
        }
