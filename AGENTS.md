# AGENTS.md — football-engine (Sporttery Engine)

> 给任何进入本目录工作的 agent 的锚点。本项目 = 竞彩足球概率分析与虚拟投注模拟系统
> （Dixon-Coles + Monte Carlo + 市场主导融合），运行在 GitHub Actions 上。
> 工作副本 = `/Users/dykily/football-engine`（2026-10-22 校正：本文原先写的
> `~/Downloads/football-engine` 与门禁命令里的 `/Users/jason/...` 都已不存在，
> 照抄第一步就会失败）。日常预测/结算/采集在云端，本机只保留 NJStats 值守。

## 开工前三步（必做）

1. **读 `PROJECT.md` 顶部交接卡**（≤15 行）；要细节按关键词查档案对应节，不通读、不重扫代码。
2. **用仓库专属 venv**（`python3.12` + requirements 全量；lightgbm 需 `brew libomp`）：
   - 运行/测试/装包一律 `<repo>/.venv/bin/python`（或 `python3.12 -m pytest`）
   - 本机裸 `python3` 是 3.13，**不许**用来跑本项目（依赖矩阵按 3.12 锁）
3. **改完必须过门禁**（全绿才算可交付；在仓库根目录执行，不要写死绝对路径）：
   ```
   ./.venv/bin/python -m pytest tests/ -q && ./.venv/bin/python -m ruff check --select F engine scripts tests evolution
   ```

## 硬规则（本项目语境）

- **评估层禁触**：`engine/backtest/`、账本（`data/state/review_ledger.jsonl`）、
  基线/校准状态文件、`engine/prediction/calibration.py` —— 改它们 = 改考卷，一律人工 review。
  自动补丁侧由 `evolution/harness/patch_engine.FORBIDDEN` + `.github/CODEOWNERS` 锁死。
- **融合链每一步都是 config 开关**（`config/prediction.json["fusion"]["post_fusion"]`），
  去留由 `scripts/ablation_replay.py` 对已结算账本做配对回放裁决，**不凭感觉加开关**。
- **实验先登记**：任何实验开工前在 `docs/EXPERIMENTS.md` 登记一行（假设/分支/主指标/判定日/样本量），
  判定标准写死后不许改；失败结论保留行、标注结果，不得删除。
- 账本 append-only；`chain=v2` = 洁净样本；污染样本（`engine/integrity/quarantine.py`）不得进校准/LGBM/回放。
- 数据产物（`data/`、`web/`）随 git 走但**不是**代码；PR 里带上它们只是分支落后 main 的噪音，
  合并前先 rebase 到最新 main。

## 交付（只走这一条路）

日常改动：本目录建分支 → 推 origin → 开 PR → 人工/闭环 review 后合入 main。
**本机没有 `gh` CLI**（2026-10-22 实测 `gh not found`）——文档里那些
`gh api ...` / `gh pr ...` 在这里都跑不了。替代路径：`git push` 后用
`~/.football-engine/github_token` 走 `curl https://api.github.com/...`，或让用户手工开 PR。
**不要因为"命令写在文档里"就假设它可用。**

日常预测/结算/采集全部在 GitHub Actions（daily-forecast / ci-sync / health-check）。
本机唯一的定时任务是 NJStats 值守 `scripts/njstats_duty.py`（站方要浏览器登录态，无法上云）；
除此之外本机不许跑预测管线——那正是历史上"两台机器各跑各的"乱源。
值守与调度表见 `docs/DUTY_RUNBOOK.md`。

推送走 SSH deploy key（remote 别名 `github-football-engine`，account `wlrwx`）。

## 调度现状（2026-10-22 重排）

- launchd `com.football.duty` 每小时 :05/:35 **直接**跑
  `./.venv/bin/python3 scripts/njstats_duty.py`。
  `harness/run_duty.sh` 壳脚本已退役（DeepSeek 值守 agent 因 key 从未存在而全程降级，是死代码），
  它里面那把 `mkdir` 并发锁已搬进脚本本身：`~/.football-engine/duty.lock`（固定路径，
  不随 TMPDIR 变——三个入口的 TMPDIR 不一定相同，路径不一致就等于各锁各的）。
  **别再回到"只有壳脚本有锁"的状态**：现在 launchd 与 Qoder 自动化会撞车，撞车是常态。
- Qoder 自动化两个：「竞彩值守 巡检」每日 4 次（补跑 duty + 查日志空缺/快照入库/配额）、
  「E8 前瞻实验每日对账」每日 22:40。每小时的确定性**留在 launchd**
  （站方 session 约 60 分钟空闲即死，不适合交给 LLM 一天跑 48 次只为执行一个脚本）。
- 原「两台机器分工」表（主线 Mac `~/Downloads/football-engine` + Mac Air 7×24 挂机）
  已随机器迁移失效：本机路径不是那个，且 2026-10-19 起 Air 的本地 cron 被清过。
  **Air 现在是否还在跑、跑什么，待用户确认后再补回本文件，不要凭记忆写。**
- 预测挑战提交器代码/状态机在 `scripts/njstats_challenge/`（README.md + submit.js）；
  需要重建时按该目录来，不要凭记忆重写。

## 收工后（必做）

更新 `PROJECT.md` 顶部交接卡（状态/下一步/新坑），本轮有价值的结论写进对应节：
现象→根因→修法→`文件:行`。**卡片、档案与代码同一批 commit 提交。** 不更新卡 = 本轮没收工。
