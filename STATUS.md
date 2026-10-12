# 足球引擎 · 状态文档（新对话开场先读这个）

> 最后更新: 2026-10-11 | 项目: 《王者万象棋》无关，这是竞彩足球预测引擎

## 一句话现状

竞彩足球概率预测 + 虚拟投注系统，GitHub Actions 全自动运行。
**vs 自身旧版 IMPROVED**（配对 ΔBrier 0.062, t=3.3, n=558，2026-10-11 修正口径后重算）；
**vs 去水市场 TIED**（Δ=+0.0045, t=1.23，无证据 edge）——有效融合权重 market 93.75%，
DJYY 第三通道曾静默死亡 6 周（10-11 已修复回填+报警），待真实样本重新定价。

## 关键路径

| 内容 | 位置 |
|---|---|
| 引擎本体 | `~/football-engine/`（engine/ + config/ + data/） |
| 四轮升级文档 | `docs/UPGRADE_20260829.md`、`docs/UPGRADE2_20260829.md`、`docs/CONTEXT_DATA_20260830.md` |
| 实时状态 | `data/state/`（calibration_status / lgbm_status / upgrade_tracker / selfcheck_report） |
| 训练账本 | `data/state/review_ledger.jsonl`（append-only，chain=v2 = 洁净样本） |
| 体检脚本 | `scripts/upgrade_tracker.py`、`scripts/edge_calibration.py`、`scripts/ablation_replay.py` |

## 自动化（重要）

- **每周一 03:30 周度体检**（升级效果+EV校准+情境覆盖）——确认它是否还挂在旧对话的工作区；
  若失效，在新对话用 CronCreate 重建，提示词要点在 docs/UPGRADE2 的路线图段落
- GitHub Actions 每半小时跑 daily-forecast（预测+结算+自检+部署 Pages；schedule 实际延迟 2~4h）
- 本地 `auto_run.sh` 每小时实时监控（含代理自愈：7897 不通自动拉起 Clash Verge）
- **系统医生 `engine/doctor.py`（2026-10-11 起）**：auto_run 每轮末尾自动体检+安全自愈——
  git 卡死清场/积压补推/refspec 修复、补抓赛果、**补结算**（结算本地化由此覆盖）、
  DJYY 存活率、CI 停摆、概率分布先验带、权重归一、cron 存在性、代理、仓库体积。
  报告: data/state/system_health.json。
  设计: 幂等自愈 + critical 返回 2（供 CI 门禁）+ 每条检查对应一次真实事故
- **仓库专属 .venv（2026-10-11 起）**：py3.12 + requirements 全量（sklearn/lightgbm 可用，
  lightgbm 需 brew libomp）。auto_run 探测顺序 .venv 优先——本地进化决策不再依赖
  hermes venv 的残缺环境；本地现在也能跑 LGBM 训练/校准拟合（与 CI 同基线）
- **推送走 SSH deploy key（2026-10-11 起）**：远端 = `git@github-football-engine:...`，
  密钥 `~/.ssh/football-engine_deploy`（只授权本仓库，经 ssh.github.com:443 + Clash 代理）。
  cron 后台会话拿不到 keychain 凭据，别把推送改回 HTTPS+osxkeychain

## 自进化机制现状（数据纪律，勿手动干预）

- 融合权重：每次结算自动调优（champion 0.05/0.75/0.20）
- 校准层：自动启用/停用决策——3 次评估均理性维持关闭（不显著不开）
- LGBM：已首训+被影子验证诚实拒绝（vs市场 Δ=-0.018），继续攒样本等它显著
- 每次结算自动写 chain=v2 洁净标记

## 已修过的大坑（防止重蹈）

- DJYY 断供两周三层根因（2026-10-09 全修）: ①限流(2400+调用/天→按天缓存)
  ②队名匹配(竞彩缩写/译名→前缀规则+别名) ③API schema迁移(referee→referees.main、
  injuries→home.sidelined)
- push 卡死 detached HEAD（2026-10-11 两次复发，晚20:25终修）: auto_run 的 pull --rebase
  停在空提交上残留 rebase-merge → HEAD 游离。自愈代码其实凌晨就加了，但埋在
  `if ! git pull | tee` 里——测的是 tee 的退出码，bash 管道取末位退出码，**自愈分支
  是从未执行的死代码**。终修: PIPESTATUS 判 git 退出码 + 启动时兜底清场。
  monitor 的 python 版自愈用 returncode 判断，一直是好的
- cron 推送拿不到凭据（2026-10-11 修）: keychain 条目和设置都正常、GUI 会话能推，
  但 launchd 后台会话 osxkeychain 静默返回空 → "could not read Username: Device not
  configured"。凌晨起挂了数小时，根因未完全定位（疑似 keychain 授权弹窗未被应答后拒绝）。
  终解: 注册 deploy key 走 SSH（见"自动化"），与 keychain/登录状态彻底解耦
- DJYY 第三通道数据断链（2026-10-11 修）: 8/16~10/9 账本 djyy_prob 全 null——CI 屏蔽期
  生成的预测无 DJYY→结算写 null→融合优化器把 DJYY 权重压到 0（有效 0%，模型仅 6.25%）。
  修: post_match 结算按 _djyy_id/match_id 双键从 context_cache 回填（djyy_backfilled
  溯源标记）+ selfcheck 存活率报警（近3天中2天<50%）。历史缓存无 model_probs 不可救
- stakes 字段（动机/情境）DJYY 上游恒 null（2026-10-11 抽样证实）: comparison API 有 key
  不填值，store-only 不进模型，周检覆盖率别拿它当健康指标
- CI 侧 djyylive 屏蔽 Azure IP 段 → 字段保全方案(本地富化+CI不覆盖, 双端各取所长)
- 本地调度: cron :00 盘口监控 + :25 完整管线(auto_run.sh, 含 DJYY 富化)
- 两个本地 cron 均带代理自愈(Clash 关闭自动拉起)
- 国庆停售三层修复(优雅跳过/非竞彩过滤/残留清理)
- CI schedule 实际延迟 2~4 小时（GitHub 调度延迟，非故障）→ 当日结算滞后属正常，
  可本地补: fetch_sina_results.py --date X && python -m engine.main --settle --date X（幂等）

- isotonic platt 分支 shrink 未定义（30≤n<100 触发）→ 已默认值+try/except
- selfcheck 小样本日（<5场）全缺误阻断 → 已加门槛
- LGBM save() API 不对称（LGBMClassifier 无 save_model）→ 已修
- CI 挂了别慌：先看失败步骤（守门员拦截=设计行为），再查日志

## 本地保险库 `_vault/`（不入公开仓库，同机 agent 可读）

站点侦察细节/密钥位置/挑战策略与配额状态全在这里；公开仓库只留泛化结论。
文件索引：recon_njstats.md（端点+JWT+规则）、recon_djyy.md（screen 契约+交叉验证）、
recon_njstats_matrix.md（16/16 矩阵）、challenge_state.json（配额）、
njstats_challenge_submit.js（v2 提交器）、njstats_value_model.json（1,667 注战绩）、
njstats_exports/sample.xlsx（Excel schema）、close_calibration_notes.md（研究笔记）。

## 下一步/开放事项（2026-10-12 晨更新）

- [ ] NJStats 试用窗口（10-15 00:08 到期）：归档管线每小时断点续采（match/odds/8概率端点
      ×分页）；概率端点的历史页在深页，逐小时补齐后做他们的信号质量评估（Brier vs 赛果）
- [ ] 预测挑战：每日 11:30 自动提交已建（EV 策略：只提赔率∈[1.60,2.31]且模型概率最高的场次，
      每日≤5 场、周配额 28，状态 _vault/challenge_state.json）；10-13 凌晨首轮结算后复盘命中率
- [x] CLV 三源交叉验证（2026-10-12）：DJYY 均价 -12.45% / football-data 平博 -6.27%（258注）/
      NJStats 平* -9.22%（28注）；**锐度梯度：澳*软庄 -3.29% → 平*锐线 -9.22%，6pp 差=
      市场水位差**。教训固化：量 CLV 必须用最锐基准（scripts/njstats_clv_check.py）
- [x] DJYY 数据门户审计：1,667 注（底价 +13.57% / 可执行收盘 +7.21% / 实盘 -8.7%），
      每月随管线自动重审（scripts/djyy_audit.py）
- [ ] SportMonks Growth 试用激活（用户暂缓）→ 验 odds 模块是否随套餐
- [ ] git 历史端点路径清洗决策（HEAD 已清，历史待用户裁决是否 force-push 重写）
- [ ] AUT 平局纸面证据累计（双源佐证 +7.5%/+9.24%，t≈1.7 候选，live 证据持续累计）
- [ ] 洁净样本累积 / LGBM ready / Qwen 新闻源（用户决定）

## 云端化与凭证现状（2026-10-12 下午补录）

- **GitHub Actions 三条管线**：daily-forecast（预测+结算，原有）、njstats-archive（试用窗口归档）、
  **ci-sync.yml（新）**：盘口监控/SportMonks 采集/NJStats 归档/每日 fdc+纸面+DJYY 审计/CLV/体检
  ——Mac 关机不断采（预测挑战除外：需浏览器登录态，留本地）
- **CI Secrets**：NJSTATS_KEY ✓、SPORTMONKS_TOKEN ✓（均 API 端加密注入）；
  ⚠ **DJYY_PROXY 从未设置**——daily-forecast 引用它但 secret 不存在，CI 侧 DJYY 富化
  一直静默直连被 403（Worker URL 在用户 Cloudflare 账号，待用户提供后一条命令补上）
- **SSH deploy key**：本地推送走 git@github-football-engine:（密钥仓库外，勿改回 HTTPS+keychain）

## 预测挑战状态（2026-10-12 晚）

- 本周 11/28 已提交，段位**挑战者**、周榜 **#12**、排位分 140
- 实测规则：**每场仅一市场**（提过即消失）；低赔带豁免只适用胜平负；
  让球按水位口径记录（主队-1@0.95），真实给分待 10-13 结算校准
- 自动化 v2（每日 11:30）：跨 胜平负/让球 择优（大小球观察不提交），
  提交器 `_vault/njstats_challenge_submit.js`（带让球线校验防符号错位）

## 数据资产现状（2026-10-12 晨）

| 资产 | 规模 | 生命周期 |
|---|---|---|
| football-data 历史 | 60,912 场含平博收盘 | 永久 |
| NJStats 归档 | 596 场×8 庄×带赛果（赔率）+ 概率端点深页补齐中 | **试用到期后失去更新** |
| DJYY 价值模型 | 1,667 注记录 | 永久（公开） |
| SportMonks 免费档 | 丹麦超/苏超阵容伤停 24 文件 | 永久 |
| AUT 平局纸面 | 1,022 注 | 永久 |
