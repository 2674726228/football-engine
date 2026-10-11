# 足球引擎 · 状态文档（新对话开场先读这个）

> 最后更新: 2026-10-11 | 项目: 《王者万象棋》无关，这是竞彩足球预测引擎

## 一句话现状

竞彩足球概率预测 + 虚拟投注系统，GitHub Actions 全自动运行。
洁净样本 **550 场**（2026-10-10 tracker: n=529 上 **IMPROVED 判定成立，z=3.14**）：
Brier 0.6172→0.5573，命中率 51.6%→55.95%，**持续小幅跑赢纯市场**（同期市场 0.5631/55.7%）。

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

## 下一步/开放事项

- [ ] 洁净样本继续累积（550→更大），跟踪 upgrade_tracker 判定
- [ ] LGBM 每次结算自动重训（本地缺 lightgbm，训练在 CI 侧），盯 ready=true（需双基线显著）
- [ ] 可选：Qwen 新闻→情境层管道（等用户定新闻源）
- [x] 用户侧遗留→已解锁：本地推送已不依赖旧 PAT（deploy key 接管），keychain 里的
      GitHub token 现在可以随时撤销重发，不影响自动化
