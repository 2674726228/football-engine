from __future__ import annotations
"""预测模型抽象基类"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TeamRating:
    """球队评级

    injury / rest_days 的历史：这两个字段自 2026-07-20 首次提交起就存在，
    但**从未被任何代码写入过** —— `EloUpdater.update()` 只更新
    elo/attack/defense/form，于是 team_ratings.json 里 1359 支球队的
    injury 恒为 0.0、rest_days 恒为 3，`injury_weight` / `rest_weight`
    两个配置项在 dixon_coles.py:130/131/142/143 里永远乘 0。
    （evolution 的 DEAD-FEATURES-REST-DAYS 探针报的就是这件事。）

    rest_days 已于 2026-10-14 由 `EloUpdater._refresh()`
    真正写入（结算时按 match_date 刷新，封顶 14 天）。
    injury 仍保持 0.0 —— 伤停信号走的是另一条已生效的路径：
    main.py:390-399 把 DJYY 的缺阵攻击手数乘到 attack 上，那条路径有
    实测数据支撑；而"缺阵人数 → 单一 injury 标量"这个映射没有证据，
    不该凭空接线。字段保留是为了不破坏 team_ratings.json 的既有 schema。

    `last_match_date` 是 rest_days 的计算依据（不参与预测，仅落盘）。
    """
    name: str
    elo: float = 1500.0
    attack: float = 1.0
    defense: float = 1.0
    form: float = 0.0
    injury: float = 0.0
    rest_days: int = 3
    last_match_date: str | None = None


@dataclass
class MatchPrediction:
    """单场比赛预测结果"""
    match_id: str
    home_team: str
    away_team: str
    competition: str
    # 胜平负概率
    home_win_prob: float = 0.0
    draw_prob: float = 0.0
    away_win_prob: float = 0.0
    # 期望进球
    home_xg: float = 0.0
    away_xg: float = 0.0
    # 让球概率（可选）
    handicap_home_prob: float = 0.0
    handicap_draw_prob: float = 0.0
    handicap_away_prob: float = 0.0
    # Top-3 比分预测（蒙特卡洛输出）
    top_scores: list = None
    top_total_goals: list = None
    # 元数据
    model_name: str = ""
    confidence: float = 0.0


class PredictionModel(ABC):
    """预测模型接口"""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def predict(
        self,
        home: TeamRating,
        away: TeamRating,
        market_odds: tuple[float, float, float] | None = None,
        handicap: float | None = None,
        is_neutral: bool = False,
        is_knockout: bool = False,
    ) -> MatchPrediction:
        """预测单场比赛"""
        ...

    def predict_batch(
        self, matches: list[dict]
    ) -> list[MatchPrediction]:
        """批量预测（默认逐个调用）"""
        results = []
        for m in matches:
            pred = self.predict(
                home=m["home"],
                away=m["away"],
                market_odds=m.get("market_odds"),
                handicap=m.get("handicap"),
                is_neutral=m.get("is_neutral", False),
                is_knockout=m.get("is_knockout", False),
            )
            pred.match_id = m.get("match_id", "")
            pred.home_team = m["home"].name
            pred.away_team = m["away"].name
            pred.competition = m.get("competition", "")
            results.append(pred)
        return results
