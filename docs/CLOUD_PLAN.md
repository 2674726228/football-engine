# 云化盘查与规划（2026-10-15 审计）

## 盘查结论：迁移前，"关机即停摆"的资产共 5 项
Mac cron×2（监控:00/管线:25 含全部收割研究）、ZCode cron×3（挑战提交/周一体检/红果无关）。
CI 侧当时仅预测+结算+Pages+ci-sync+归档(已过期仍在撞墙：23/50 失败)。

## 已完成的迁移（本次）
| 能力 | 原来 | 现在 |
|---|---|---|
| 挑战 1x2 提交 | Mac ZCode 提示词 | **CI challenge.yml 每小时:35 + scripts/challenge_hourly.py 确定性代码** |
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
- [ ] challenge_hourly 让球缺口：CI 需线路/水位 → 已用 Mac 补位；观察两周后决定是否给 openApi dialog 端点做纯 API 版（协议已在 vault，可行）
- [ ] git 历史端点清洗决策（用户）：force-push 打断并行 agent 风险 vs 历史泄露面；建议维持现状（HEAD 已净+端点本身半公开）

**该优化（两周内）**
- [ ] 状态文件并发写：CI/Mac 双端写 challenge_state → 给两边都加 pull-rebase-commit 事务（现依赖小时错峰，够用但可加锁）
- [ ] eval_plane 的 quorum 加"合议方向+置信"一行判定（现在只列票）
- [ ] CI 摘要页周报化：weekly-deep-check 输出与上周对比 diff（趋势一眼可读）

**该升级（看数据说话）**
- [ ] NJStats 续订评估（试用已到期收割完结）：等前向配对样本 ≥80 注后由 challenge_review 的 edge 兑现率裁决——**独立信息型燃料值 698 就续，市场镜像型不续**
- [ ] AUT 平局：live 证据 ≥80 注做第一次去留裁决（当前 1,022 历史注 +4.64%）
- [ ] 新燃料管线化：SportMonks Growth（用户择时）；新闻/首发源（Qwen 挂起项）接入时按"输入层热插协议"走，别碰评测层
- [ ] 仓库瘦身触发器：doctor 400MB 报警在盯，到线再议 LFS

## 原则不变（写死）
评测/预测双管道各自多源；模型永不读挑战/评委产物（CI 防火墙执法）；任何单点（含挑战本身）死亡=降级不是事故。
