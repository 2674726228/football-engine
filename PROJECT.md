# PROJECT.md — football-engine（竞彩足球概率引擎）

> [!IMPORTANT] 交接卡（收工必更新；冷启动只读这块）
> 状态: doing　更新: 2026-10-13 11:40（逆向轮 / 首次真实提交成功）
> 在干什么: **挑战任务首次真实提交成功**——5 注全部落库（服务端配额 usedCount 0→14），整条链路打通
> 下一步: ① 把这个任务部署到 Air（launchd 每天 11:25；步骤见 README「登录态怎么来」）
>          ② **token 20 小时过期**——Air 上需每天刷新一次（localStorage 的 `token` 键，不是 cookie）
>          ③ 额度恢复后补满 27 个 prob 分页缺口（后台循环在跑；10-15 到期前完成即可）
>          ④ Air 上停掉除预测挑战外的本地 cron（auto_run/run_realtime_monitor/launchd）
> 已知坑: ① **token 只能留在 Air**：20h 有效期 + CI 无法无人值守刷新；主线 Mac Pro 非常开，不能承担定时任务
>          ② **`git reset --hard` 会清掉归档**（已发生两次）——归档已移出索引，reset 前先跑 `njstats_safeguard.py`
>          ③ 提交成功的判定是 `msg="suc"`，**不是 `code===0`**（成功时不返回 code 字段）——写反会把成功记成失败、破坏幂等
>          ④ 归档进度标记会与磁盘脱节 → 已加每轮自愈对账；代理曾硬编码 → 已改探测
> 待验证: ① 让球票「真实给分」机制（STATUS.md：待结算校准），此前让球 EV 排序可能高估
>          ② 竞彩池内只有欧冠有 jcSpf/jcRq；其余 214 场只有欧赔口径（口径择优已实现）
>          ③ 首次真实提交的 5 注结算结果（10-13 凌晨后）

- 更新: 2026-10-13（主线 agent 统一入口轮）

## 这是什么
竞彩足球概率分析与虚拟投注模拟：Dixon-Coles 泊松 + 蒙特卡洛 + 市场主导融合 + Kelly 虚拟下注。
全自动跑在 GitHub Actions（预测/结算/采集/自检/Pages 报告），无服务器。**仅数据分析与算法学习，不构成投注建议。**

## 结构速查
- `engine/main.py` — 每日管线入口（抓数据→融合→锁定→结算）
- `engine/prediction/` — `dixon_coles.py` / `monte_carlo.py` / `fusion.py`（纯函数+开关+trace）/ `signals.py` / `score_candidates.py`
- `engine/backtest/` — 回测与实验（**评估层，禁自动改**）：`gate_reachability.py`(E3) / `book_vig.py`(E4) / `cross_market.py`(E2)
- `engine/review/post_match.py` — 结算写账本（PR#29 在此补全审计链）
- `engine/integrity/quarantine.py` — 污染样本隔离（巴甲平局概率被钉 0.5025 的 20 场）
- `engine/odds_bands.py` — 赔率分层 L1-L5 单一真源（与闸门边界 1.8 对齐）
- `evolution/` — 自治进化闭环（诊断→补丁→验证→开 PR；三道锁见 `harness/patch_engine.py`）
- `config/prediction.json` — 模型/融合参数；`config/strategy.json` — 下注策略
- `data/state/review_ledger.jsonl` — 训练账本（append-only，chain=v2 洁净）
- `docs/EXPERIMENTS.md` — 实验登记表（开工前登记，判定日写死不改）

## 环境事实
- Python 3.12（CI 同版本）；本机裸 python3 是 3.13，勿用于本项目
- 依赖：numpy/scikit-learn/LightGBM/joblib（lightgbm 需 `brew libomp`）【易变：brew 源】
- 推送：HTTPS token 或 SSH deploy key；STATUS.md 记录走 `~/.ssh/football-engine_deploy`【本机未验证】
- GitHub Actions：daily-forecast(:15/:45) / ci-sync(:47) / health-check(:00) / evolution-loop(每日 01:30 UTC，PR#28 新增)
- GitHub 对公开仓高频 schedule 有排队限流：实测 */15 实际只跑 5 次/天，阈值必须按真实频率设

## 已知未修（按优先级）
- [ ] issue #1（外部用户 dzd1234 报）：竞彩未开盘但 predictions.json 有胜平负推荐 → 需复现"无盘口场次仍出 1X2 推荐"的路径
- [ ] `kelly.py` 拿 `1/odds`（含 12.1% 抽水）当市场概率，与 `clv.py`/`upgrade_tracker.py`/`post_match.py` 的去水口径不一致 → 改口径会放宽闸门 1.4~2.6 倍，**需人拍板**（PR#27 文档已标注，故意未改）
- [ ] 开盘价列（PSH/PSD/PSA）样本为 0：`_new.csv` 有收盘无比分旧格式有开盘无比分 → 要开收盘对比须先合并两套 schema
- [ ] `evolution/` 每轮在 main 上直接跑 `python -m evolution.cycle`，产物进 `evolution/out*`（已 gitignore）

## 已修（现象 → 根因 → 修法）
- 2026-10-13 **挑战任务首次真实提交成功**：真 token 下 5 注全部落库（服务端配额 usedCount 0→14，浏览器直查后端确认）。过程中发现**成功判定写反**——`GMI_SUBMIT` 成功时不返回 `code` 字段（只回 `msg="suc"`），原判定 `code===0` 把成功记成失败、破坏幂等（`scripts/njstats_challenge/submit.js::isSubmitOk`）
- 2026-10-13 token 有效期实测 **20 小时**（JWT iat→exp=72000s）→ 推翻"搬进 CI"方案：CI 无法无人值守刷新，任务必须留在 7×24 的 Air（`scripts/njstats_challenge/README.md`）
- 2026-10-13 **别名匹配打通价值兑付**：挑战任务"命中 0/5"根因是**队名译写差异**（非联赛不重叠）——竞彩用简称（比利亚雷/巴萨/里斯本/莱红牛/马竞/加拉塔萨），池子用全称。改用手写归一化 → 复用 `engine/team_aliases.py` 三级匹配（别名表/loose/内置兜底）+ 补齐欧冠主流简称 → **命中 5/5，全部走竞彩口径**（`scripts/njstats_challenge/submit.js`）
- 2026-10-13 归档安全网：`git reset --hard` 清掉归档（第二次发生，靠备份救回 124 文件）→ 加 `njstats_safeguard.py`（只增不删/拒写仓库内/每轮自动跑）+ 归档进度每轮自愈对账（`scripts/njstats_safeguard.py`）
- 2026-10-13 归档跨机器故障：代理硬编码 7897 + key 文件名跨脚本不一致（`njstats_key` vs `njstats_apikey.txt`）→ 两处都**静默零产物**（不报错）→ 改为探测/多文件名兜底（`scripts/njstats_archive.py`）
- 2026-10-13 挑战 guessingId 映射打通：用真实 apikey 调 GMI_MATCH_LIST(listType=OPEN) 拿 222 场，按 schema 逐层解出 guessingId+中文队名+开赛时间+三盘口赔率；weekId 编码实测=YYYY+ISO周号（2026-10-13→202642）；配额实测 maxCount=28/本周已用0。**同时发现池内 8 场欧冠才有竞彩盘口（jcSpf/jcRq 带让球线），我们 10 场匹配 0 命中（联赛不重叠）**——不是没注可提，是没预测欧冠 → `matchPool()` 加盘口口径择优（有 jc 用 JC_SPF/JC_RQ，否则欧赔）（`scripts/njstats_challenge/submit.js`）
- 2026-10-13 预测挑战协议实证：v3 端点是推断的 → 逆向前端 bundle 挖出真实协议（`POST /njserver/openApi/` + `application/x-protobuf` + modId/msgId/Bearer），实测「合法 protobuf + 无 token → 200 + 请先登录」证明链路正确 → 改用手工 protobuf 编解码 + `--probe` 子命令；5 条新测试锁 wire format/有符号 code/枚举/pick 口径（`scripts/njstats_challenge/submit.js`）
- 2026-10-13 Air 收口：预测挑战提交器（`_vault/njstats_challenge_submit.js`）从未入仓库，只在 Air 本地磁盘，Air 一出问题这个唯一无法上云的任务就断了且仓库无记录 → 根因：`_vault/` 被 gitignore，关键任务成了"黑工" → 修法：按 STATUS.md 记录的实测规则重建为 `scripts/njstats_challenge/submit.js` + README，补 7 条回归测试锁住周界/择优/幂等/让球符号
- 2026-10-13 `weekKey()` 时区坑：用 `getDay()` 算周界，北京周一 07:00 = UTC 周日 23:00 → 被算成上一个周一，周一早上的提交记到上周配额（部分重复计算、部分凭空消失）→ 修法：shift 到 +08:00 再取 UTC 字段（submit.js）
- 2026-10-13 `submit.js` 的 ROOT 基准：`path.resolve(__dirname,'..','..')` 在源码被载入的上下文里 `__dirname` 失效，ROOT 静默跑偏 → loadCandidates 报「无预测文件」而真实原因是路径基准丢了，错误信息毫无指向性 → 修法：优先用 `process.argv[1]` 推导，并加 `test_root_points_at_repo_root` 锁住（已验证能抓住该 bug）
- 2026-10-13 PR 乱局收口：6 个 open PR 中 3 个是重复/悬空重建版（分支落后 main 68 笔后 CONFLICTING，只能开新分支重放）→ 根因是 `data/`+`web/` 有每小时自动提交，分支不及时 rebase 就必然悬空 → 修法：三有效 PR 按 #29→#28→#27 合入（merge-tree 预验证互不冲突），4 个悬空 PR 关闭+注释去向，8 个遗留分支删除，开 PR 前必先 rebase 写进 AGENTS.md
- 2026-10-13 `evolution/` 16 处 ruff F 级告警 CI 完全不可见 → 根因：#28 把 `evolution/**` 挂上触发路径但 lint 步骤没跟着扩（"跑了"不等于"查了"）→ 修法：test.yml ruff 范围加 `evolution`，含 F601 重复 dict 键（`known_issues.py` 的 detect_by/invariant_id/candidate 被静默覆盖）、F821 缺 `import random`
- 2026-10-12 CI 重试循环从不 abort → Daily Forecast 永久失败；`|| echo` 吞掉失败把毒状态传给下游 → 失败必须 abort + 显式告警（daily-forecast.yml）
- 2026-10-12 测试并发组跨 PR 互相取消 → group 是全局 `sporttery-tests` + cancel-in-progress → 按 PR 号分组（test.yml）
- 2026-10-12 评估类报告从未被 CI 刷新 → 修复只进 PR 没进生产（PR#6 的修复在生产从未生效）→ 报告生成步骤挂进 ci-sync
- 2026-10-11 健康检查 60 分钟阈值是死逻辑 → 声明 */15 实际 4~5 小时才跑一次 → 阈值按真实频率设（health-check.yml）
- 2026-10-11 账本把「1X2 方向命中」当「下注命中」→ 聚合口径混淆两件事 → 写入端修 + 逐场落盘审计字段（PR#29）
- 2026-10-11 三套赔率分层口径不一致（账本 2.0/3.0 vs ev_report 1.8/2.2 vs 闸门 1.8）→ 账本 1.5-2.0 桶横跨闸门边界，L3 缺口结构性不可见 → `odds_bands.py` 单一真源（PR#29）
- 2026-10-11 DJYY 断供 6 周静默（名义权重 0.20 实际贡献 0）→ 融合层归一化兜住但无任何运行时信号 → 逐场落盘 `fusion_effective_weights` + 死源告警（PR#29）
- 2026-08/10 巴巴西平局概率被钉 0.5025（真实 0.266）→ `league_draw_baseline` 把"判平精度"当平局率 → bug 已修，但 20 场污染样本仍在 v2 链 → `quarantine.py` 按 match_id 隔离（PR#29）

## 决策与理由
- 融合权重市场主导（model 0.05 / market 0.75 / djyy 0.20，实际 6.25%/93.75%/0）：426 场账本实证纯市场 Brier 0.583 优于任何模型流（0.646+）
- 污染样本按 match_id 剔除而非升 chain：污染仅 20/558 条（3.6%），升 chain 会作废全部样本重来（2026-08-29 已做过一次）
- PR#27 **故意不改** `kelly.py` 的含抽水口径：E0~E2 全负时放宽闸门只会增加亏损注，这是需要人拍板的取舍
- 三道锁（PR#28）：C1 白名单禁触评估层（代码级）+ C2 自动 PR 强制反事实对照（bootstrap CI）+ C3 CODEOWNERS 锁给人（流程级）—— 因为闭环会朝"指标好看"的方向进化，没有约束它就是奖杯机
- 本目录 `~/Downloads/football-engine` 定为唯一工作副本（2026-10-13）：本地 cron/launchd 全停，日常循环全在 GitHub Actions，避免多机器各跑各的

## 下次注意
- 开 PR 前先 rebase 到最新 main：`data/`+`web/` 有每小时自动提交，落后 6~14 笔就会带上几千行噪音 diff
- 合并顺序 #29 → #28 → #27（三者互不冲突，但 #27 的 `docs/EXPERIMENTS.md` 与另两者无交集，顺序不影响）
- 合并 #29 后头几天重点看：巴甲平局概率是否回升、总进球类指标是否短期偏高（`xg_calibration` 回收的已知代价）
