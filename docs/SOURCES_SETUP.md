# 双数据源接入操作清单（2026-10-11 深挖后）

> 背景：CLV 三线证据（tracker TIED / #6 33k 场回放负 / 下注级 -12.45%）确认系统无 edge，
> 破局靠新信息源。两个候选源的接入与验证路径如下。

## 1. SportMonks（数据底座：xG/阵容/伤停/Pinnacle 赔率）

### 注册后立即做（免费档，¥0）
```bash
# 把后台拿到的 API token 存好（不入 git）：
mkdir -p ~/.football-engine && echo "你的token" > ~/.football-engine/sportmonks_token && chmod 600 ~/.football-engine/sportmonks_token
```
然后跑一次探针（免费档=丹麦超/苏超）：
```bash
cd ~/football-engine && ./.venv/bin/python3 -c "
from engine.sources.sportmonks import SportMonksClient
import json; print(json.dumps(SportMonksClient().verify_access(), ensure_ascii=False, indent=1))"
```

### 过零点激活 Growth 试用（€99/30联赛，14天，免信用卡）
- 试用档位选 **Growth**（竞彩实测横跨 18~30 联赛，Starter 5 个不够）
- **选联赛清单**（竞彩常卖，SportMonks 命名）：
  英超 Premier League、英冠 Championship、西甲 La Liga、西乙 La Liga 2、
  意甲 Serie A、意乙 Serie B、德甲 Bundesliga、德乙 2. Bundesliga、
  法甲 Ligue 1、法乙 Ligue 2、荷甲 Eredivisie、荷乙 Eerste Divisie、
  葡超 Primeira Liga、日职 J1 League、日乙 J2 League、韩K1 K League 1、
  韩K2 K League 2、瑞典超 Allsvenskan、挪超 Eliteserien、芬超 Veikkausliiga、
  巴甲 Serie A Brazil、美职 MLS、瑞士超 Swiss Super League、奥甲 Austrian Bundesliga、
  苏超 Scottish Premiership、中超 Chinese Super League、
  欧冠 UEFA Champions League、欧联 UEFA Europa League、欧协联 Conference League、
  国际赛/友谊赛 International Friendlies
- ⚠ 核验项：**Odds（含 Pinnacle）是否随 Growth 附带**（文档与营销页冲突）；
  若需单独加购 Standard Odds，记下价格再决定
- 激活后无需其他操作：`auto_run.sh` 的采集已挂好（token 门控），每小时自动拉
  当日竞彩比赛的赔率序列（含 Pinnacle 1X2）+ 预测/xG/阵容/伤停，
  落 `data/state/sportmonks/{date}_{fixture_id}_*.jsonl|json`
- 核验探针（试用激活后跑）：
  `./.venv/bin/python3 -c "from engine.sources.sportmonks import SportMonksClient; print(SportMonksClient().verify_access(probe_fixture_id=<某场竞彩比赛的djyy_id>))"`

### 关键事实
- **djyy_id == SportMonks fixture_id**（33/33 恒等实测，DJYY 透传上游 ID）——
  我们预测记录的 `_djyy_id` 可直接查 SportMonks，无需映射表

### ⚠ 免费档实测权益矩阵（2026-10-11 深夜探针，token 已存）
| 模块 | 免费档 | 说明 |
|---|---|---|
| fixtures/联赛/积分 | ✓ | 4 个联赛可见（丹麦超/苏超+ playoffs） |
| 阵容 lineups.player | ✓ | include 可用 |
| 伤停 sidelined | ✓ | include 可用 |
| xG (xgfixture) | ✗ 403 | 明确 "no access"，付费附加项 |
| Odds（含 Pinnacle） | ✗ 模块未订阅 | 附加项 €14~69/月 |
| Predictions | ✗ 404 | 附加项 €9~99/月 |

- **SportMonks 是模块化计费**：Growth €99 只是底座（fixtures/阵容/伤停），odds/predictions/
  xG 全是独立附加包。全栈 ≈ €99+€24(odds+preds)+€15(xG) ≈ **€138/月（~¥1070）**
- 明天激活 Growth 试用后重跑探针：odds/predictions 若仍 404 → 看后台附加包实价再裁决

## 2. NJStats（竞彩原生信号：凯利/风控/指数异动/中文概率）

### 前端接口逆向结论（2026-10-12 凌晨，匿名实测）
- `GET /api/client/match/leagues` ✓ 匿名可用（联赛清单，含中文名）
- `POST /api/client/match/ALL/list` ✓ 匿名可用但**数据全抹**：345 场/152 字段的骨架，
  队名 None、xG/potentials/odds_list 全 0——字段名暴露了他们的数据模型
  （team_axg_pre_match、o25/btts/u35_potential、pre_match_ppg），确认试用接口会带这些值
- 竞彩指数走 `POST /njserver/openApi/` **protobuf 私有协议**；`/api/sportApi/realtime` 401
- **结论：匿名层无可用增量（竞彩指数我们已有 sina 直连），一切价值在登录后的试用窗口**

### 注册后做（3 天试用从注册时点起算）
1. 后台拿 API key → `export NJSTATS_TOKEN=...`（或告诉我，我按同模式接）
2. 下载示例 Excel（无需会员）→ 确认下载数据的字段 schema
3. 看「购买/升级」页：API 个人版 ¥698/月 与 下载数据月卡（30次/月）的最终价
4. 把 key 给我 → 建njstats source（按 djyy 模式）→ 试用窗口内跑 A/B 测量

### 试用窗口收割清单（3 天，别浪费）
- **/prob**：每日竞彩场次的概率信号（o25/u25/btts/主客龙/平局）→ 与 DJYY model_probs
  同场对照（独立 Brier）
- **/odds/list**：36*/平* 初盘+赛前+凯利+风控系数 → Pinnacle 基准 CLV 的第二来源
- **/team/recent/list**：近 3-10 场 vs 赛季差异% → LGBM 候选特征
- 每次 capture 落 data/state/njstats/，窗口结束后统一裁决

## 3. 验证裁决标准（试用窗口内完成）

| 测量 | 方法 | 判定 |
|---|---|---|
| 概率信号质量 | /prob(或 predictions) vs DJYY model_probs vs 市场去水，同场次独立 Brier | 谁显著低谁有真信号 |
| CLV 基准升级 | CLV 报告换 Pinnacle 收盘基准，对比现 DJYY 均价口径 | 口径锐化程度 |
| 情境特征增量 | 近期状态差异% 加入 LGBM 特征，walk-forward Brier 变化 | 显著改善才留 |

## 4. 成本决策树

- SportMonks 免费档验证集成 → Growth 试用实测 → 确认 Odds 附带且数据有用 → €99/月
- NJStats：3 天试用测出独立信号 → ¥698/月；无信号 → 放弃（或参加预测挑战赢 Pro）
- DJYY：降级备份，不再维护其 schema 漂移
