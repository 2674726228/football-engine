# 值守 runbook（NJStats 挑战链）

> 2026-10-22：本文件取代 `harness/`（DeepSeek 值守 agent + run_duty.sh 壳脚本），
> 那套东西整个赛季没真正跑过——`~/.football-engine/deepseek_key` 不存在，
> 每一轮都在 `无 DeepSeek key，降级跑确定性脚本` 分支上，等于死代码。
> 调度改由两处承担：launchd（直接调 venv 跑脚本，每小时 :05/:35）
> + Qoder 自动化「竞彩值守 巡检」（每日 03/09/15/21 点 :35，补跑一轮并核对日志/快照）。

## 一、主链是什么

每小时 :05/:35 跑一次 `scripts/njstats_duty.py`（仓库 venv，python3.12）：

```
cookie(session) → mint 新 JWT → JWT 变了就推 GitHub secret NJSTATS_JWT
→ 跑 scripts/challenge_hourly.py（1x2 提交器）
→ 服务端真值快照 + 站点赔率快照 + 社区倾向快照
→ commit_push（state 与三份快照都会入库，2026-10-22 修）
→ CI 看门狗（CI 超 3h 没跑就补触发一轮）
```

退出码：0 正常 / 20 需要人工播种登录。

## 二、并发锁（为什么在脚本里而不是在壳脚本里）

`scripts/njstats_duty.py::acquire_lock()` 用 `mkdir` 原子锁（macOS 自带的
`flock` 只在部分版本有），锁龄 >30 分钟视为陈锁自动清理，`finally` 释放。

历史上这把锁只在 `harness/run_duty.sh` 里，脚本本身无锁——
PROJECT.md 记的「下一步② duty 与 launchd 并发无锁」就是这个。
实测撞过一次：08:35:03（手动）与 08:35:10（launchd）并存约 7 秒，
两个进程同时 `git pull --rebase --autostash` + 写 `njstats_jwt` +
写 `challenge_state.json`。壳脚本退役时如果锁不跟着搬，
删掉的就是唯一那份并发保护。现在有两个调度源（launchd + Qoder 自动化），
撞车是常态而不是意外，锁必须在脚本里。

`DUTY_SKIP_LOCK=1` 仅供手工调试，且必须明确自己不会撞车。

## 三、闸门口径（2026-10-22 起为 ev_site）

`data/state/challenge_policy.json`：

```json
{"gate_mode": "ev_site", "ev_floor": 0.02}
```

- `edge`（默认，历史上用过）：`P − market_fair`，与赔率无关。
  实测 7 场未开赛对照里**全为负**（最大 −0.23pp），1.5pp 档从 10-19 起
  连续 131 轮零合格。这不是"门槛太高"，是量纲绑错了（与 E3 结论一致）。
- `ev_site`：`P × 站点结算价 − 1 ≥ 2%`，上界 25% 判脏价。
  赌的是盘口软（站点价比体彩价中位高 11.6%），与模型能力无关。
  **去留由 E8 裁决，判定标准已写死在 docs/EXPERIMENTS.md，不许边跑边改。**
- 回退：把 `gate_mode` 改成 `"edge"`（或删掉该键）即停实验，一行改完。

红线：零合格是正常状态，永远不为凑数降门槛。

## 四、排障对照

| 现象 | 含义 | 处理 |
|---|---|---|
| `mint 失败` / HTTP 401 | 浏览器 session 死透（remember-me 也过期，约 14 天） | 用户跑 `./fe-agent seed`（= `scripts/njstats_seed_login.py`）。**绝不尝试自动过验证码** |
| `推 secret → 401` / `Bad credentials` | GitHub token 失效 | 更新 `~/.football-engine/github_token`（`./fe-agent ghtoken`） |
| `SSLError` / `ReadTimeout`（www.njstats.cn） | 站点侧抖动 | 下一轮自愈，记录即可 |
| `blocked_by_edge` / DJYY 403 | Cloudflare 边缘拦截（CI 侧） | 已知问题，见 docs/EXPERIMENTS.md 的 DJYY 节；**不要在值守里改 cf-proxy** |
| `⚠ 取不到服务端已预测列表` | msgId=11 拿不到 | 本轮退回纯本地 state 去重，重复投注风险升高，看一眼 JWT |
| 整小时日志空缺 | 机器合盖/未运行 | 站点赔率快照靠这个频率攒样本，漏轮=漏样本；如实报告 |

## 五、调度表现状（2026-10-22）

| 入口 | 频率 | 干什么 |
|---|---|---|
| launchd `com.football.duty` | 每小时 :05/:35 | 直接 `./.venv/bin/python3 scripts/njstats_duty.py`（确定性，不走 LLM） |
| Qoder 自动化「竞彩值守 巡检（每日 4 次）」 | 03/09/15/21 点 :35 | 补跑一轮 + 按小时统计日志空缺 + 查快照入库 + 查配额 |
| Qoder 自动化「E8 前瞻实验每日对账」 | 每日 22:40 | 样本量、命中率 vs 打平线、服务端/本地账目对账 |
| GitHub Actions | 见各 workflow | 预测/结算/采集/体检/Pages（daily-forecast 实际只跑 12/48 次，平台限流） |

每小时的确定性为什么留在 launchd：站方 session 约 60 分钟空闲即死，
降到每天几次就要人工重新播种登录；而让 LLM 一天跑 48 次只为执行一个脚本也不划算。
代价是**本机合盖就漏轮**（2026-10-21 实测 20:00/21:00 两个整点零日志）——
漏的是赔率快照样本，不是资金，可接受但要如实报出。

`launchctl unload ~/Library/LaunchAgents/com.football.duty.plist` 停本机值守；
Qoder 侧在 Automations 里 disable 对应任务。两边都停 = JWT 20h 后死，需人工 `./fe-agent seed`。

## 六、凭据与文件位置

全在 `~/.football-engine/`（仓库外，0600）：
`njstats_session.json`（cookie，站方约 60 分钟空闲即死，故 :05/:35 双频）、
`njstats_jwt`（名义 20h，服务端有宽限）、`njstats_account`、`github_token`、
`pwprofile`（remember-me 无头续期用）、`duty.log`（心跳，排障先看它）。

站方协议细节（protobuf / msgId / JWT 续期链）在
[docs/CHALLENGE_SUBSYSTEM.md](CHALLENGE_SUBSYSTEM.md) 与 `scripts/njstats_api_submit.py`，
不在本文件重复。

## 七、退役记录

- 2026-10-19 `harness/deepseek_agent.py` 上线（替代 ZCode 人工值守）。
- 2026-10-19~22 全程 `无 DeepSeek key`，从未进入 agent 分支。
- 2026-10-22 删除 `harness/`（含 run_duty.sh 壳、BRIEF.md、duty_prompt.md）。
  ZCode 时代的本地任务库与 cron 备份移到 `~/.football-engine/retired-20261022/`
  而不是直接删，需要时还能翻。
- 同时纠正 BRIEF.md 里的过时描述：它写着「板源 msgId=1 全板，勿用已死的
  msgId=11」，而 2026-10-21 的 bcb9f2c98 已把**去重真源**改成 msgId=11
  （账号私有「我已预测」列表）。两者都对，但对象不同：
  候选板用 msgId=1，去重用 msgId=11。
