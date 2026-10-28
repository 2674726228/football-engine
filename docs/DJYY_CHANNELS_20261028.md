# DJYY 通道现状与「要不要再加源」（2026-10-28）

> 结论先行：**接口没有不稳，是可达性分层 + 失败不可见。**
> 加源解决不了这个问题；先让两条通道各自可判定，再谈第三家。

## 1. 通道拓扑（现在其实只有一条半）

```
              ┌─ 实时 API  djyylive.com/api/leagues/fixtures + /api/match/{id}/comparison
              │    本地/桌面：稳定 200
              │    GitHub Actions：Cloudflare Bot Fight Mode → 403（blocked_by_edge）
              │    经 cf-proxy Worker：上游对 Worker 出口是**间歇** 403（实测 6 次约 2 次）
              │
fusion djyy ──┤
              └─ 仓库快照  data/djyy_matches.json
                   由 fetch_djyy_ssr.py 抓取后 commit（本机 duty 或 CI 步骤）
                   下游 main.py:509 在实时源缺失时补位
```

两条通道**都指向同一个域名**，所以它们不是真正独立的：域名被拦时，
快照能不能更新，取决于「本机 duty 还活着」这一件事。

## 2. 历史覆盖率（E1，来自每日 predictions 与账本 djyy_prob）

| 区间 | 覆盖率 | 说明 |
|---|---|---|
| 07-19 ~ 08-15 | 30–100%（均值约 50%） | 正常，缺的部分是 DJYY 不覆盖的联赛 |
| **08-16 ~ 09-30** | **连续 46 天 0%** | CI 侧 403，**没有任何告警** |
| 10-10 ~ 10-19 | 13–88%（均值约 62%） | 修预算与可见性之后 |
| 10-20 ~ 10-21 | 0% | 再次中断两天 |
| 10-22 / 10-23 | 84.6% / 66.7% | 10-23 实时源仍 403，靠快照补位 8/12 |

## 3. 2026-10-28 实测（本机，非 CI）

| 探测 | 结果 |
|---|---|
| `/api/leagues/fixtures` × 8 | 8/8 = 200，0.89–3.55s，20 场，`has_odds: true` |
| `/api/match/{id}/comparison` × 6 | 6/6 = 200，模型三项和 0.9999，markets 10 个 |
| `djyydata.com` SSR 首页 | 200，但 12.5s |

结论：**源本身健康，问题只在 CI 出口可达性。**

## 4. 本次改动（本 PR）

| 文件 | 改动 | 解决什么 |
|---|---|---|
| `scripts/fetch_djyy_ssr.py` | 每次抓取结果写 `data/djyy_snapshot_status.json`（含 `ok` / `reason` / `at_epoch` / 盘上快照真实年龄）；异常也写；状态文件跟随 `OUTPUT` 目录 | 失败此前只有一行 stdout + workflow 里的 `\|\| echo ⚠`，CI 恒绿 |
| `.github/workflows/health-check.yml` | 新增「快照通道」步骤：抓取失败 → `::error`；状态 30h 未更新 → `::error`；盘上快照 >24h → `::error` | 「实时被拦 + 快照死」= 双通道全断，必须红 |
| `engine/main.py` | 状态文件无论成败都写 `snapshot_*` + `live_channel_used` + `fallback`（`live`/`ssr_snapshot`/`none`） | 以前只在补位成功时写，导致「实时活着时无法判断快照死没死」；两条都死时一个字段都不写 |
| `scripts/djyy_market_paired_test.py` | 新增配对 Brier 检验（配对 t + bootstrap CI） | 0.3 权重从来没有和实测对照过 |

## 5. 权重该不该是 0.3？配对检验结果（n=209，三路 Brier 齐全）

| 对阵 | 均值差 | 95% CI | p | 判定 |
|---|---|---|---|---|
| djyy − market | +0.48pp | [−2.12, +3.05] | 0.718 | 无显著差异 |
| djyy − model | −4.74pp | [−8.65, −0.99] | 0.017 | **djyy 显著更好** |
| model − market | +5.22pp | [+1.50, +8.96] | 0.006 | **model 显著更差** |

读法：djyy 与市场同水平（差 0.48pp，CI 跨 0），比自家模型好近 5pp。
所以「djyy 值得留」成立，但**它提供的主要是市场的重复信息**；
真正拖后腿的是 model（比市场差 5pp，权重却还有 0.1）。
`ablation_report.json` 的权重搜索给 `model 0.0 / market 0.6 / djyy 0.15`，与此一致。

注意样本选择偏差：三路齐全仅 209/1171 行（17.8%），因为 8/16–9/30 djyy 长期为 null。
这份数据只代表「djyy 有数据的那批比赛」，不等于全赛季。

## 6. 要不要再加源

| 选项 | 定位 | 成本 | 结论 |
|---|---|---|---|
| football-data.co.uk | 免费收盘赔率，做 CLV/回测独立基准 | ¥0 | **阻塞**：全英文队名，`team_aliases` 是纯中文表（约 160 条），要做中英映射才能对上竞彩简称。需先定「联赛范围」与「映射口径」 |
| SportMonks 付费 | 与 djyy **同源同 ID**（`djyy_id == fixture_id` 33/33），替掉第三方前置 | €138/月 | 已排除（要出钱） |
| NJStats | 不同信号（凯利/风控/指数异动） | ¥698/月 | 已排除（要出钱） |
| 同域名 SSR/API 双写 | 不增加信息量 | ¥0 | 已覆盖（快照通道） |

## 7. 复现命令

```bash
# 实时源是否活着（本机）
curl -s -o /dev/null -w "%{http_code} %{time_total}\n" \
  "https://djyylive.com/api/leagues/fixtures?date_from=$(date +%F)&date_to=$(date +%F)"

# 配对检验
python scripts/djyy_market_paired_test.py --window 30 --json data/state/djyy_paired_test.json

# 快照通道状态
cat data/djyy_snapshot_status.json
```
