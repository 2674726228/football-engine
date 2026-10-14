"""新浪赔率加载 + 单场匹配（从 engine/main.py 抽出）

load_sina_odds_map: 读取 data/daily/{date}/odds_sina.json，建立
(队名) 与 (竞彩编号) 两套索引。文件不存在或解析失败返回空 dict（容错，
与旧行为一致——调用方按 missing 处理即可）。

lookup_sina_match: 给定一场比赛，在上面两套索引里按优先级找赔率。
原先是 main.py 预测循环里 ~35 行内联代码（2026-10-14 抽出），
三级 fallback 的顺序是行为契约，改动前先看下面的注释。
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def load_sina_odds_map(target_date, root: Path | None = None) -> tuple[dict, dict, int]:
    """加载指定日期的新浪赔率。

    Returns:
        (sina_odds_map, sina_odds_by_no, n_matches):
          sina_odds_map:   {(home_team, away_team): match}（队名索引，fallback）
          sina_odds_by_no: {match_no: match}（竞彩编号索引，优先匹配）
          n_matches:       成功解析的场次数（用于日志；0 = 缺失或解析失败）
    """
    root = root or Path(__file__).resolve().parent.parent.parent
    sina_odds_map: dict = {}
    sina_odds_by_no: dict = {}
    sina_odds_file = root / "data" / "daily" / str(target_date) / "odds_sina.json"
    n_matches = 0
    if sina_odds_file.exists():
        try:
            sina_data = json.loads(sina_odds_file.read_text(encoding="utf-8"))
            n_matches = len(sina_data)
            for m in sina_data:
                # 按队名索引（fallback）
                sina_odds_map[(m.get("home_team", ""), m.get("away_team", ""))] = m
                # 按竞彩编号索引（优先）
                match_no = m.get("match_no", "")
                if match_no:
                    sina_odds_by_no[match_no] = m
        except Exception:
            pass
    return sina_odds_map, sina_odds_by_no, n_matches


def _norm_name(s: str) -> str:
    """队名极简归一化：只留中日韩文字与英文字母。

    比 engine/team_aliases.normalize_team 更激进（会主动删 FC/队/市），
    用于**模糊**匹配那一级。激进是有意的：这一级本来就是兜底，
    宁可多几个候选让调用方自己判，也不要漏。
    """
    s = s.replace("FC", "").replace("队", "").replace("市", "")
    s = re.sub(r'[^\u4e00-\u9fffa-zA-Z]', '', s)
    return s.strip()


def lookup_sina_match(fixture, sina_odds_map: dict, sina_odds_by_no: dict) -> dict | None:
    """按优先级找一场比赛的新浪赔率。

    三级 fallback 的顺序是**行为契约**，不能调换：

      1. 竞彩编号精确匹配（最可靠——编号来自竞彩官方，与新浪同源）
      2. 队名精确匹配（fixture 的原始字符串 == odds_sina 的原始字符串）
      3. 队名归一化匹配（走 engine/team_aliases，吃译名差异）
      4. 队名模糊匹配（_norm_name 后互相包含；最激进，最后才用）

    为什么 4 必须在 3 后面：模糊匹配是双向包含（"奥林匹亚" in
    "奥林匹亚科斯" 或反过来都算），会把 "A" 和 "A B" 这种**不同球队**
    判成同一场。放在最后意味着只有前三层都失败时才接受这个风险。

    返回 None 表示四层都没匹配上（调用方按"无新浪赔率"处理）。
    """
    from engine.team_aliases import normalize_team   # 避免模块级循环依赖

    # 1) 竞彩编号: "2026-08-01_周六001" → "周六001"
    match_no = fixture.match_id.split("_", 1)[-1] if "_" in fixture.match_id else ""
    if match_no:
        hit = sina_odds_by_no.get(match_no)
        if hit:
            return hit

    # 2) 队名精确
    hit = sina_odds_map.get((fixture.home_team, fixture.away_team))
    if hit:
        return hit

    # 3) 队名归一化（译名变体，如 奥林匹亚 vs 奥林匹亚科斯）
    hk_n, ak_n = normalize_team(fixture.home_team), normalize_team(fixture.away_team)
    for (sh, sa), v in sina_odds_map.items():
        if (normalize_team(sh), normalize_team(sa)) == (hk_n, ak_n):
            return v

    # 4) 队名模糊（互相包含）
    ht, at = _norm_name(fixture.home_team), _norm_name(fixture.away_team)
    if ht and at:
        for (sh, sa), v in sina_odds_map.items():
            sh_n, sa_n = _norm_name(sh), _norm_name(sa)
            if (ht in sh_n or sh_n in ht) and (at in sa_n or sa_n in at):
                return v
    return None


def build_sina_data(sina_match: dict | None, match_id: str) -> dict | None:
    """把 odds_sina.json 的一条记录转成预测用的 _sina_data 结构。

    与 lookup_sina_match 分开是有意的：查找逻辑变了要能单独测，
    字段组装（尤其 series_features 的调用点）变了也要能单独测。

    series_features 在**这里**调而不在 lookup 里调：水位时间序列是
    按 match_id 索引的独立数据源，与"找到哪条 odds_sina 记录"无关。
    放在这里意味着即使四层 fallback 换了实现，序列特征也不会丢。
    """
    from engine.odds_series import series_features   # 延迟导入：重依赖

    if not sina_match:
        return None
    return {
        "initial_odds": sina_match.get("euro", {}).get("initial"),
        "current_odds": sina_match.get("euro", {}).get("current"),
        "movement": sina_match.get("euro", {}).get("movement"),
        "compression": sina_match.get("euro", {}).get("compression"),
        "odds_history_count": len(sina_match.get("odds_history", [])),
        "asia": sina_match.get("asia"),
        "totals": sina_match.get("totals"),
        "match_time": sina_match.get("match_time"),
        # 水位时间序列特征（2026-08-05 盘口系统修复）：
        # 由 data/state/odds_series/ 累积快照计算，识别赛前资金流斜率/加速。
        # 特征存 predictions 供结算验证命中率，不预设有效/无效。
        "series": series_features(match_id),
    }
