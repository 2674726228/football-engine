# 云化盘查与规划（2026-10-15 审计）

## 盘查结论：迁移前，"关机即停摆"的资产共 5 项
Mac cron×2（监控:00/管线:25 含全部收割研究）、ZCode cron×3（挑战提交/周一体检/红果无关）。
CI 侧当时仅预测+结算+Pages+ci-sync+归档(已过期仍在撞墙：23/50 失败)。

## 已完成的迁移（本次）
| 能力 | 原来 | 现在 |
|---|---|---|
| 挑战 1x2 提交 | Mac ZCode 提示词 | **确定性代码 challenge_hourly.py；云 challenge.yml:25 与 Mac 值守同码互备**（:25 而非 :35：本机 duty 在 :05/:35 写同一份 state，CI 开始真提交后同分钟必撞车；云遇 runner IP 封锁时 Mac 兜底，恢复即自动切换，零人工） |
| 挑战 让球提交 | 同上混跑 | Mac 值守（弹窗线路/水位需浏览器），与 CI 共用 repo state |
| JWT 凭据链 | 不存在 | Mac 每小时检测续推 GH secret（云端零浏览器依赖） |
| 周一深度体检 | Mac ZCode cron | **CI weekly-deep-check.yml**（六份评测 JSON 回仓+摘要页）；Mac 已改退役占位 |
| 到期资产止血 | njstats-archive 每小时撞墙失败 | 工作流文件迁至 docs/workflows-retired（调度摘除，方案留档） |
| 收割/研究/监控 | 仅 ci-sync | ci-sync 保留 + daily-forecast + health-check：关机也跑的已有 4 条 |

## 现在仍需 Mac 的（如实清单）
1. 让球市场提交（浏览器弹窗）+ JWT 续推（登录态在浏览器里）→ **挂掉后果：1x2 照常、让球停更，无系统损伤**
2. Clash 代理与本地管线（belt-and-suspenders 冗余；CI 已能独立供数）
3. Evolution Loop 的本地数据依赖（云端跑但吃本地 push 的 predictions——若 Mac 长期关机，CI daily-forecast 自己生成预测，链路仍通）

## 路线图（该做/该修/该优化/该升级）
**该修（本周）**
- [ ] Evolution Loop 上云首验：确认云端 checkout 能自取 DJYY 富化（SSR 已通，engine 批量仍被 CF 速率限→分片节流优化）
- [x] challenge_hourly 让球缺口：已用 Mac 补位；**新发现：njstats.cn 对 GitHub runner IP 的列表请求失败（疑墙），与 djyylive 同族**——已定双端同码互备：Mac(:35) 先兜 1x2+让球，云 challenge.yml 保留调度，连续 3 天云成功再降级 Mac 为只管让球（降级条件写进 Mac cron 简报）
- [ ] git 历史端点清洗决策（用户）：force-push 打断并行 agent 风险 vs 历史泄露面；建议维持现状（HEAD 已净+端点本身半公开）

**该优化（两周内）**
- [ ] 状态文件并发写：CI/Mac 双端写 challenge_state → 给两边都加 pull-rebase-commit 事务（现依赖小时错峰，够用但可加锁）
- [ ] eval_plane 的 quorum 加"合议方向+置信"一行判定（现在只列票）
- [x] ~~CI 摘要页周报化~~ 2026-10-22：weekly-deep-check 增加 `scripts/weekly_digest.py --write`，
      指标与上次周检对比（Δ 列）。基线存独立历史 `data/state/weekly_digest.jsonl`——不能读 git 上一版，
      因为 upgrade_tracker / clv_report 每天都会刷新，上一版不是「上周」；首次运行只记基线。
      回仓步改为逐文件 add，避免单个文件缺失让整条 `git add` 原子失败。

**该升级（看数据说话）**
- [ ] NJStats 续订评估（试用已到期收割完结）：等前向配对样本 ≥80 注后由 challenge_review 的 edge 兑现率裁决——**独立信息型燃料值 698 就续，市场镜像型不续**
- [ ] AUT 平局：live 证据 ≥80 注做第一次去留裁决（当前 1,022 历史注 +4.64%）
- [x] ~~新燃料管线化：SportMonks Growth（用户择时）~~ 2026-10-22 裁决不订阅（现有 token 仅丹麦超/苏超 4 联赛，xG 403、Pinnacle 赔率不可得；DJYY 后端同源，快照已覆盖，试用到期即断供）。**新闻展示层 2026-10-23 已落地**：直播吧足球列表 → `data/daily/<date>/news.json` → 页面「赛前情报」，store-only；进模型仍按"输入层热插协议"走，先有覆盖率再预登记，别碰评测层
- [ ] 仓库瘦身触发器：doctor 400MB 报警在盯，到线再议 LFS

## 原则不变（写死）
评测/预测双管道各自多源；模型永不读**裁判产物**（挑战三件套/结算结果/方向真值，CI 防火墙执法），赛前公开的侧挂数据可进但只走 `njstats_side.py` 且受时点与市场隔离约束（见 `docs/CHALLENGE_SUBSYSTEM.md`）；任何单点（含挑战本身）死亡=降级不是事故。

## 盘查二轮（2026-10-15 增补）

- **修（已修）**：NJStats 归档覆写事故——按日期命名的文件存的是"抓取时刻快照"，赛前抓会把赛后带比分的行刷回"未开赛"。已从 git 历史抢救回 10-09~10-12 的 988 条完赛行；`scripts/njstats_archive.py` 加了守卫（窗口左沿外日期禁止重抓覆写）。
- **裁决顺延**：配对复盘发现该站"模型概率列表"与"赛果列表"覆盖的是不相交的比赛集合，唯一可行路径是**两阶段协议**（赛前冻结快照 → 赛后重抓配对）。已把当日赛前快照冻结进 vault；试验 key 过期后该裁决暂时挂起，若将来有数据窗口再按协议执行。核心结论不受影响（无 edge 共识与 AUT 平局段都建立在 fdc/DJYY 数据上）。
- **观察**：Mac :35 值守首轮尚未产出新挑战记录（配额停在 11/28，等今晚触发验证）；CI runner 被对方边缘规则拦着，双端互备按设计等 3 个绿灯日自动回归。
