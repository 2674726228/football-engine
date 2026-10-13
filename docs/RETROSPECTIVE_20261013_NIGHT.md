# 逆向轮复盘 · 2026-10-13 夜（NJStats 全链路 + 收尾）

> 取证式：所有数字来自当场验证（API 实测 / 测试套件 / git / 文件系统），非记忆。
> 上一轮复盘见 `RETROSPECTIVE_20261012.md` 与 `REVIEW_20261013.md`。

## 0. 三句话

任务要求"把 NJStats 一并逆向，核心是以后都能拿到关键数据"。结果：
**协议全部挖通并实测（含提交端点），拿到 222 场全市场池的 guessingId 映射；
但发现一个决定性事实——我们预测的联赛与 NJStats 开放池完全不重叠，
池内唯一有竞彩盘口的 8 场欧冠我们没预测，所以"有注可提但提不了"。**
逆向本身完成度 100%，价值兑付卡在**联赛覆盖**上，这是下一轮的靶心。

## 1. 逆向成果（全部实测，非推断）

### 1.1 数据 API（apikey，只读）
| 项 | 值 |
|---|---|
| 基地址 | `https://api.njstats.cn/api/v1/` |
| 鉴权 | `?apikey=<key>`（60 次/h，`limit.remaining` 实时报） |
| 端点 | `match/list`、`odds/list`、`prob/{draw,over,under,btts,home,away,ht-goal,avg-goal}`、`team/{list,stats/list,recent/list}`、`league/list` |

**实测缺口修正**：每个 prob 端点 **571 场（maxPage=6）**，原实现只取 2 页 →
丢 65%。`match/list` 不带 date 时是全市场 654 场，从未归档。

### 1.2 挑战 API（protobuf，逆向自前端 bundle）
| 项 | 值 |
|---|---|
| 端点 | `POST https://www.njstats.cn/njserver/openApi/` |
| 请求 | `application/x-protobuf`；外层 `{params(1), page(2)}` |
| 头 | `modId: 1012`(MI_GUESSING) + `msgId` + 鉴权 |
| 读鉴权 | `apikey` 头即可（**实测可用**） |
| 写鉴权 | `Authorization: Bearer <token>` —— apikey 永远回「请先登录」 |

**msgId**：1=GMI_MATCH_LIST、2=GMI_SUBMIT、15=GMI_WEEK_PREDICT_QUOTA

**关键 schema**（逆向自 protobuf-es 生成代码）：
- `ReqGuessingSubmit{guessingId(1,str), playType(2,enum), pick(3,int32)}`
- `GuessingMatchItem{... guessingId(7,str), weekId(8,int32)}`
- `GuessingMatchOdds{spf(1), rq(2), dx(3), jcSpf(4), jcRq(5)}`
- 枚举 `GPT_JC_SPF=4 / GPT_JC_RQ=5`；`pick` 是 **1-based**（逆向自前端 `Yn()`）
- `weekId` = `YYYY + ISO周号`（2026-10-13 → 202642）

### 1.3 实测验证链
1. 合法 protobuf + 无 token → `HTTP 200 code=-1 msg=请先登录` ✅ 协议对
2. apikey 读 `GMI_MATCH_LIST(OPEN)` → **222 场**，解出 guessingId + 中文队名 + 赔率
3. apikey 读 `GMI_WEEK_PREDICT_QUOTA` → `maxCount=28, usedCount=0, remainCount=28`
4. apikey 打 `GMI_SUBMIT` → 仍「请先登录」→ **提交必须浏览器登录态**（结论确定）

## 2. 决定性发现：联赛不重叠

| 池子 222 场联赛 | 我们预测的联赛 |
|---|---|
| 欧青联28 / 欧冠18 / 欧协联18 / 欧联18 / 亚冠二级16 / 美职联15 / 英冠12 / 亚冠10 / 土甲10 / 德丙10 / 日联杯8 / 挪甲8 | 意甲 / 西甲 / 巴甲 / 英超 / 瑞典超 / 葡超 / 法乙 / 亚冠精英 |

**匹配结果：10 场 vs 222 场，命中 0。**

但池内 **8 场欧冠带真实竞彩盘口**（jcSpf/jcRq，jcRq 带让球线 -1/-2/+2/+3）：

| 场次 | jcSpf 主胜 | 落在 [1.60,2.31] 带内？ |
|---|---|---|
| 比利亚雷亚尔 vs 那不勒斯 | **2.04** | ✅ |
| 莱比锡红牛 vs 埃因霍温 | **1.60** | ✅（下边界） |
| 马德里竞技 vs 曼联 | 1.78 | ✅ |
| 国际米兰 vs 布鲁日 | 1.18 | ❌ |
| 阿森纳 vs 里尔 | 1.16 | ❌ |

→ **不是没注可提，是我们没预测欧冠。** 这解释了任务为何长期"正常空跑"没人发现。

## 3. 数据资产（试用 10-15 00:08 到期）

### 3.1 归档覆盖（导出时点）
- 归档文件 **198 个**，覆盖 6 天（10-09 ~ 10-14）
- 派生特征 **1573 条**（`data/state/njstats_features/`）
- prob 分页缺口 **30 个**（每缺 1 页 ≈ 100 条），后台循环正在补

### 3.2 prob 记录的价值（此前被低估）
每条含：
- 主客队 **overall/home/away 三套完整统计**（ppg/进失球/半场/胜负平/领先落后，各 20+ 字段）
- **openingHome/Draw/Away 开盘赔率**
- drawRate

**这直接解掉 STATUS.md 记的老问题**："开盘列（PSH/PSD/PSA）样本为 0，
旧格式文件又没有比分，要做开收盘对比必须先合并两套 schema"——
NJStats 的 prob 端点开盘价与赛果**同源**，不需要合并 schema。

## 4. 本轮修复的缺陷（全部有测试）

| # | 缺陷 | 根因 | 影响 |
|---|---|---|---|
| 1 | prob 只存 2/6 页 | 分页写死 page 1-2 | 丢 65% 数据 |
| 2 | 全市场横截面从未归档 | 无 snapshot 任务 | 无法发现漏掉的联赛 |
| 3 | 代理硬编码 7897 | 换机器不开 Clash | **静默零产物** |
| 4 | key 文件名跨脚本不一致 | `njstats_key` vs `njstats_apikey.txt` | **静默跳过归档** |
| 5 | 进度标记污染 | 旧 2 页时代 done 标记 | 新分页逻辑跳过补页 |
| 6 | `extract_team(team,"all")` 取空 | 真实键名是 `overall` | 全局统计静默为空 |
| 7 | 无盘口场次不标注 | 展示层缺标记（issue #1） | 读者以为能照着买 |
| 8 | 原始归档 33MB 进 git | 新目录没跟上既有政策 | 每次 clone 多背 30MB+ |

**共性**：1/3/4/5/6 都是**静默失败**——不报错，只是产出空或少。
这类错误的排查成本最高，所以每条都配了会失败的测试（而不是"跑通就算"）。

## 5. 真实事故（本轮发生并修复）

**`git reset --hard` 掉了 124 个归档文件。**
对齐远端时 reset，归档刚被 `git rm --cached` 移出索引，reset 把"已跟踪"部分回退。
救回来的唯一原因是几小时前做过导出（`cp -n` 补回）。

→ 结论：备份/导出不能依赖"想起来才做"。已加 `scripts/njstats_safeguard.py`
（只增不删、拒绝写入仓库内、每轮采集后自动跑）。

## 6. 基础设施现状

| 项 | 状态 |
|---|---|
| 仓库体积 | 清理前 68MB 原始数据（fdc 35M + archive 33M）；archive 已移出索引 |
| 归档安全网 | `njstats_safeguard.py`（增量、只增不删、git 外） |
| 到期导出 | `njstats_export.py`（含覆盖度体检 + MANIFEST + README） |
| 后台采集 | 循环等额度重置，每轮 52 次；自动跑安全网 |
| 测试 | 486 passed；ruff F 级全清 |
| 分支/PR | open PR 清零，仅 main + gh-pages |

## 7. 未完成的（按优先级）

1. **给引擎加欧冠预测** ← 唯一能解掉"有注可提但提不了"的动作
   - 池内 8 场欧冠有竞彩盘口；`football-data` 历史库应含欧冠（60,912 场那个）
   - 做完即可让挑战任务从"空跑"变成"真提注"
2. **Air 上拿 Bearer token**（apikey 过不了提交）
3. **Air 上停掉除预测挑战外的本地 cron**（与 ci-sync 抢写 `data/state/`）
4. 补满 30 个 prob 分页缺口（后台循环进行中，10-15 前完成即可）
5. issue #1 的展示层已修，但**根因**（为何会走到"无盘口仍输出概率"）值得再挖
6. `kelly.py` 含抽水口径与其它模块不一致（需人拍板，PR#27 已标注）

## 8. 给下一个 agent 的三句话

1. **协议已全部实证**，不要再逆向；直接读 `scripts/njstats_challenge/README.md`
2. **提交卡在联赛覆盖**，不在协议或凭证——先加欧冠预测
3. **归档 10-15 到期**，`--verify` 看缺口，`safeguard` 保命
