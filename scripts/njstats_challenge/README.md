# NJStats 预测挑战提交器（Mac Air 专属任务）

> 这是整个项目里**唯一不能上云**的任务，其余全部在 GitHub Actions。
> 本目录是 2026-10-13 从 Air 本地 `_vault/` 重建进仓库的版本（原文件从未入 git）。

## 为什么它必须在 Air 上

预测挑战要**浏览器登录态**（Bearer token），GitHub Actions 的 runner 是一次性容器，
没有登录态，也无法人工过验证码。所以这个任务只能留在 7×24 挂机的 Mac Air 上。

## 已验证的真实协议（2026-10-13 逆向 + 实测）

从 NJStats 前端 bundle（`/app/index-*.js` + `mine-*.js` + `proto-*.js`）逆向出协议，并用 curl + 真实 apikey 实测通过：

| 项 | 值 |
|---|---|
| 端点 | `POST https://www.njstats.cn/njserver/openApi/` |
| Content-Type | `application/x-protobuf` |
| 自定义头 | `modId: 1012`（MI_GUESSING）、`msgId: <接口>` |
| 读接口鉴权 | `apikey: <数据API key>`（`?apikey=` 那个 key 直接当头发也行） |
| 写接口鉴权 | `Authorization: Bearer <token>` —— **必须浏览器登录态** |
| 请求体 | protobuf：外层 `{params(1,bytes), page(2,msg)}` |
| 响应体 | protobuf：`{code(1,varint), msg(2,string), data(5,bytes)}`；`code=0` 成功 |

### msgId 一览

| msgId | 名称 | 鉴权 | 用途 |
|---|---|---|---|
| 1 | GMI_MATCH_LIST | apikey | 开放池（listType=0 OPEN / 1 GUESSING / 2 FINISHED） |
| 2 | GMI_SUBMIT | **Bearer** | 提交预测 |
| 15 | GMI_WEEK_PREDICT_QUOTA | apikey | 本周配额 |

### 实测结论（2026-10-13，真实 apikey）

1. **apikey 读得到一切，但提交永远「请先登录」**——带不带 apikey 都一样
2. **开放池 222 场**，联赛：欧青联28 / 欧冠18 / 欧协联18 / 欧联18 / 亚冠二级16 /
   美职联15 / 英冠12 / 亚冠10 / 土甲10 / 德丙10 / 日联杯8 / 挪甲8…
3. **我们侧 10 场（意甲/西甲/巴甲/英超/瑞典超/葡超/法乙/亚冠精英）命中 0 场**
   ——那些联赛池子里一场都没有
4. **但池子里有 8 场欧冠带真实竞彩盘口**（jcSpf/jcRq，jcRq 带让球线 -1/-2/+2/+3），
   例如「比利亚雷亚尔 vs 那不勒斯」jcSpf 主胜 **2.04**，落在 [1.60,2.31] 带内
   → **不是没注可提，是我们没预测欧冠**
5. **周配额**：`maxCount=28`、本周 `usedCount=0`、`remainCount=28`
6. `weekId` 编码 = `YYYY + ISO周号`（2026-10-13 → 202642）

> ⚠️ 前端 bundle 的 hash 会随发版变（`index-DIYsyTJY.js`）。协议变动时按上面的路径重新逆向：
> 搜 `ReqGuessingSubmit` / `MI_GUESSING` / `resolveProtoBaseURL`。

## 每日流程（11:30）

```
1. daily-forecast（Actions，每小时 :15/:45）产出 data/daily/<date>/predictions.json
2. Air 上先拉一次池子缓存：  node scripts/njstats_challenge/submit.js --pool
3. Air 11:30 跑:  node scripts/njstats_challenge/submit.js --date <date> --submit
4. 提交结果写 ~/.football-engine/challenge_state.json（周配额 + 已提场次）
```

**Air 上只需要这一个定时任务。** 其他本地 hourly cron（auto_run.sh / run_realtime_monitor.sh）
必须全部停掉——它们与 ci-sync.yml 每小时同时写 `data/state/`，互相覆盖。

## 常用命令

| 命令 | 需要 | 作用 |
|---|---|---|
| `--probe` | 无 | 探活：验证协议链路（期望「请先登录」） |
| `--pool` | apikey | 拉开放池 + 联赛分布，缓存到 `njstats_pool.json` |
| `--quota` | apikey | 本周配额（已用/上限/剩余） |
| `--date <d>` | apikey | dry-run：选场 + 匹配池子，**不提交** |
| `--date <d> --submit` | apikey + **Bearer token** | 真提交 |
| `--status` | 无 | 本周已提场次 |

## 提交规则（2026-10-12 实测，改规则必须改这里 + STATUS.md 两处）

| 规则 | 值 | 出处 |
|---|---|---|
| 赔率带 | odds ∈ [1.60, 2.31] | STATUS.md「EV 策略」 |
| 每场上限 | 一票（每场仅一市场，提过即消失） | STATUS.md 实测规则 1 |
| 每日上限 | 5 场 | STATUS.md |
| 每周配额 | 28 | STATUS.md + 前端文案「每周28次预测机会」 |
| 可选市场 | 竞彩胜平负 + 竞彩让球（大小球只观察不提交） | STATUS.md 自动化 v2 |
| 低赔带豁免 | **只适用胜平负**，让球没有 | STATUS.md 实测规则 2 |
| 让球记录口径 | 水位口径（主队-1@0.95） | STATUS.md 实测规则 3 |

## 让球符号校验（为什么不能删）

`validateHandicapSign()` 检查 `line` 是主队让球数：负=主队让，正=主队被让，
范围 ±5。**符号反了 NJStats 不报错，只会静默记成相反的一场**，到结算才发现全错。
这是原 v2 提交器专门加的一道校验，v3.1 保留。

## 登录态怎么来（唯一需要人工的步骤）

```bash
# 在 Air 上执行一次：
# 1. 浏览器打开 https://www.njstats.cn 并登录
# 2. DevTools → Network → 任一条 /njserver/openApi/ 请求 → Request Headers
# 3. 复制 Authorization: Bearer 后面的 token，写到 ~/.football-engine/njstats_token.txt（一行）
```

token 过期后重新导出即可。**不要**把 token 提交进仓库（`~/.football-engine/` 在仓库外）。

数据 API key（只读，拉池子/看配额用）写到 `~/.football-engine/njstats_apikey.txt`。

## 状态文件（都在仓库外，不随 git 走）

| 文件 | 内容 |
|---|---|
| `~/.football-engine/njstats_apikey.txt` | 数据 API key（只读） |
| `~/.football-engine/njstats_token.txt` | Bearer token（提交用） |
| `~/.football-engine/challenge_state.json` | 周配额计数 + 已提 match_id（幂等：提过即跳过） |
| `~/.football-engine/njstats_pool.json` | 开放池缓存（`--pool` 拉取） |
| `~/.football-engine/njstats_value_model.json` | 战绩累积（STATUS.md 记的 1,667 注） |

**Air 重装后**：本目录 + README 就是全部；token 需重新人工导出一次。

## 已知未修

- [ ] **我们侧与开放池联赛不重叠**（当前最大问题）：池子 222 场里意甲/西甲/巴甲/
      英超/瑞典超/葡超/法乙**一场都没有**，我们 10 场匹配 **0 命中**。
      池内唯一带竞彩盘口的是 **8 场欧冠**。
      → 要真正提注，要么等池子开放主流联赛，要么**给引擎加欧冠预测**
- [ ] `--submit` 需要 Bearer token（apikey 过不了提交），必须 Air 上人工登录一次
- [ ] 低赔带豁免目前没实现（统一按 [1.60,2.31] 过滤）。STATUS.md 说豁免只适用胜平负，
      若要让低赔胜平负也能提，需要在 pickBest 里给 1x2 单独放宽下界——但那会改变提交行为，
      **需人拍板后再改**
- [ ] 让球的「真实给分」机制尚未校准（STATUS.md：待 10-13 结算校准）——在此之前
      让球票的 EV 排序可能高估其价值
- [ ] 前端 bundle hash 随发版变化，协议若变需重新逆向（搜 `ReqGuessingSubmit`）

