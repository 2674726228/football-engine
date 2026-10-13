# PROJECT.md — football-engine（竞彩足球概率引擎）

> [!IMPORTANT] 交接卡（收工必更新；冷启动只读这块）
> 状态: done　更新: 2026-10-13 16:45（逆向+复核轮收尾 / 全部直接推 main，无排队 PR）
> 在干什么: NJStats 与 DJYY 两线逆向/复核全部落地：5 注真实提交（配额14/28）、开盘价 8 市场接入、
>           健康阈值/别名/same_odds 列名/snapshot_at 等 **6 个真 bug 修复**，566 tests 全绿
> 下一步: ① Air 部署挑战任务（launchd 11:25 + 每日刷 token；README 有全套步骤）
>          ② 本机会话 token 今日 23:30 过期——Air 部署前先别在本机跑 --submit
>          ③ 后台归档循环补完 snapshot×12 + 10-14 尾部 4 页（额度每小时自动续，无需人盯）
>          ④ evolution-loop 首跑诊断 DEAD-FEATURES-INJURY-REST——与 manager 已在取 sidelined 矛盾，
>             断点大概率在"enrichment 结果没进特征装配"；要人拍板才动（动=预测层改动）
>          ⑤ main.py god object（2421 行；已核实无重复去水逻辑，属维护性风险）另开一轮
> 已知坑: ① push 前必 fetch+rebase origin/main（Air 的 hourly 提交和 Actions 自动提交都在推 main）
>          ② git reset --hard 会清归档文件（发生两次，靠备份救回）——reset 前跑 njstats_safeguard.py
>          ③ prob 归档 label ≠ 市场快照时刻（已用 snapshot_at 透传；下游时序以它为准）
> 待验证: ① 让球票「真实给分」（10-13 凌晨已结算——看 NJStats 段位/给分即可校准 EV 排序）
>          ② 巴甲平局回升（10-11 后无巴甲结算样本，有样本后看 draw 概率是否离开 0.5025）
>          ③ 静默吞异常还有 ~96 处（关键路径已修 sporttery/djyy 两源；其余按触碰即修）

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
> 2026-10-13 复核：前 4 条**已解决**，移到底部「已完成」；下面是真正还开着的。

- [ ] **`main.py` god object**：`run_daily_pipeline` 1225 行 + `run_settlement` 955 行。
      风险：任何改动都要在 2000+ 行里定位，且两处逻辑容易各写一份（同口径漂移的高发区）
- [ ] SportMonks Growth 试用激活（用户暂缓）→ 验 odds 模块是否随套餐
- [ ] git 历史端点路径清洗决策（HEAD 已清，历史待用户裁决是否 force-push 重写）
- [ ] AUT 平局纸面证据累计（双源佐证 +7.5%/+9.24%，t≈1.7 候选，live 证据持续累计）
- [ ] 洁净样本累积 / LGBM ready / Qwen 新闻源（用户决定）
- [ ] SportMonks `fixtures_on` / `fixture_odds` / `injuries` 三个方法未接入。
      **本机无 token 无法验证**（SPORTMONKS_TOKEN 只在 GitHub Secret 里，
      试用激活用户已暂缓）。要动它们需先在 `~/.football-engine/sportmonks_token`
      配好 token 再实测——**不要凭文档猜**，这三个的 odds 模块"需订阅"
- [ ] 全仓库 98 处 `except → return None/pass` 静默吞异常。**这是本项目的头号病害**——
      今晚发现的三处静默零产物（代理硬编码、key 文件名、`same_odds` 列名不匹配）
      都是同一个病：不报错，只是没产出。**不要批量改**（会淹没真正需要静默的地方）；
      建议按"关键路径"逐个加可区分的错误记录，参考 `engine/sources/djyy.py` 的
      `_record_error()` 模式

## 今晚已解决（原在待办清单里，复核后移出）
- [x] issue #1 无盘口标注（PR #35：`_pred_pick` 加「⚠ 无盘口·不可投注」；页面已验证 1 处）
- [x] `kelly.py` 口径（量化裁决：**不改**，依据 docs/KELLY_CALIBER_DECISION_20261013.md）
- [x] 开盘价缺口（DJYY comparison 的 8 个市场带 Pinnacle/bet365 开盘价，**零额外请求**）
- [x] `evolution/` 产物（已 gitignore）

## 已修（现象 → 根因 → 修法）
- 2026-10-13 **静默吞异常·关键路径第一处**：sporttery（竞彩官方主源）的 fetch_results 完全静默 return []、fetch_fixtures 只 print——被 WAF 拦/代理没配/结构变更三种病因在下游全表现为「今天没赛果」→ 结算静默跳过、账本静默不涨（与 DJYY 静默 3.5 周同病）→ 照 djyy 的 LAST_ERROR 模式接上，blocked_by_waf 与 fetch_failed 可区分；写测试时又当场抓到「成功后旧错误不清会永久挂红」并补上
- 2026-10-13 same_odds._load() 列名全错：读 match_id/result/league 而 odds.csv 里是 home_score/away_score/competition → 永远加载 0 条 → analyze 恒 0 → find_value_signals 恒空，功能从未真正跑通过且不报错；已改真实列名+比分推导（**0 → 29586 条**）。教训：**评估参数必须用真实数据**——3 个样本测出「容差要 0.15」，92 场真实数据测出 0.015→14%、0.1→91%，结论相反
- 2026-10-13 死代码终审（7 个「写了从未被调用」逐个实测）：fetch_odds_snapshot 真 bug 已修（1/p 把去水概率转成公平赔率当真实赔率存，偏高 3~4%=抽水量，与 sporttery 口径不一致）；same_odds 两条已修；get_model_probabilities 标注已取代；fetch_match_events 实测正常；sina.get_available_dates 可用未接；sportmonks 三项本机无 token 不可验证（如实标注，不凭文档猜）
- 2026-10-13 evolution-loop 首跑已验证（手动 dispatch 38365314945，success）：ACCEPT 2 候选走 audit/invariant 事实通道；**auto-fix 补丁 0 个 → 不推分支不开 PR（C1/C2 三道锁行为正确）**。其诊断 DEAD-FEATURES-INJURY-REST（1374 队 injury 全零）与现状矛盾——fetch_match_info 的 sidelined 数据有、manager 也在取 → 断点在 enrichment→feature 一环，记入待办需人拍板
- 2026-10-13 全盘复核：① **待办清单陈旧**——4 条早已完成却仍挂在"已知未修"（issue #1/kelly 口径/开盘价/evolution 产物），会让下一个 agent 重做已完成的事，已清理并标注 ② 全仓库扫出 **7 个"写了却从未被调用"的方法**，其中 `DJYYSource.fetch_odds_snapshot` 有真 bug：`1/p` 把去水概率转回的"公平赔率"当成真实赔率存（实测偏高 3~4%=抽水量），与 sporttery 口径不一致 → 已改用 `markets.raw_odds` ③ `main.py` 2421 行（两个 1000+ 行函数）经核实**无重复去水逻辑**，属维护性风险而非正确性漂移
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
