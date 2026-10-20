#!/usr/bin/env python3
"""挑战自动提交 · CI 端（确定性，1x2 市场；无浏览器无对话）

分工：CI 跑胜平负（数据全在仓库可定；本脚本）；让球市场需弹窗线路水位，
留给 Mac 端 ZCode cron（仅让球模式）——两侧共用 repo 内 challenge_state.json，git 同步。
凭据 env NJSTATS_JWT（Mac 每日 08:15 续推 secret）；缺失/过期→静默跳过退 0。
门槛逻辑与 docs/CHALLENGE_SUBSYSTEM.md 三层合同一致（只写策略层状态）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import njstats_api_submit as api  # noqa: E402

STATE = ROOT / "data" / "state" / "challenge_state.json"
POLICY = ROOT / "data" / "state" / "challenge_policy.json"
BJT = timezone(timedelta(hours=8))


def mult(o):
    if o < 1.60: return 0.0
    if o < 1.80: return 1.2
    if o < 2.31: return 1.4
    if o < 3.51: return 1.6
    if o < 4.51: return 1.8
    return 2.0


def iso_week(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"week": None, "quota": 28, "used": 0, "records": []}


def main() -> int:
    jwt = os.environ.get("NJSTATS_JWT", "").strip()
    if not jwt:
        print("challenge: 无 NJSTATS_JWT（待 Mac 续期），跳过")
        return 0
    now = datetime.now(BJT)
    st = load_state()
    wk = iso_week(now)
    rolled = st.get("week") != wk
    if rolled:
        st["week"], st["used"] = wk, 0
        if st.get("mode") == "W42-fill":
            st["mode"] = None
    # 周翻转必须立刻落盘（2026-10-19 修）：此前 STATE.write_text 只在「提交成功
    # 注数 > 0」的路径上执行，而零合格/已满/列表失败都会提前 return 0——
    # 于是周翻转后的 week/used 从未持久化。现场：state 停在 W42/27/28 且
    # mode 仍是 W42-fill，直到某轮投出 ≥1 注才整份写回自愈。
    # 注意别把影响说大：门槛没坏。翻转在算 g 之前，g 取自内存里翻转后的
    # mode，duty.log 的「门槛1.5%」即证。坏的是落盘状态——任何直接读该文件
    # 的消费者（人/看板/复盘脚本）会拿到错的周次配额，残留 mode 还会误导。
    # 这里只写「翻转」这一个动作，不触碰 records，避免零合格轮制造噪音提交。
    if rolled:
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    remaining = st["quota"] - st["used"]
    if remaining <= 0:
        print(f"challenge: 本周已 {st['used']}/{st['quota']}，满")
        return 0
    fill = st.get("mode") == "W42-fill"
    g = 0.001 if fill else (0.015 if now.weekday() <= 3 else 0.008)
    try:
        pol = json.loads(POLICY.read_text()) if POLICY.exists() else {}
        if pol.get("gate_hint") == "tighten" and (pol.get("n_settled") or 0) >= 80:
            g += 0.005
    except Exception:
        pass

    items = api.board_items(jwt)
    if items is None:
        print("challenge: 列表失败（JWT 过期？），本轮跳过，明日 Mac 续期自愈")
        return 0
    pred = {}
    for d in (now.strftime("%Y-%m-%d"), (now + timedelta(days=1)).strftime("%Y-%m-%d")):
        pf = ROOT / "data" / "daily" / d / "predictions.json"
        if not pf.exists():
            continue
        try:
            ps = json.loads(pf.read_text(encoding="utf-8"))
            ps = ps.get("predictions", ps) if isinstance(ps, dict) else ps
            for p in ps:
                if p.get("home_team"):
                    pred[(p["home_team"], p["away_team"])] = p
        except Exception:
            continue
    # 去重名单 = 本地 state ∪ 服务端已预测列表。
    #
    # 只靠本地 state 是不够的：CI 与本机是两个真正独立的仓库副本，各自
    # pull/push 同一份 challenge_state.json；两边同时跑到同一个候选时，
    # 谁先 push 谁赢，另一个仍会按自己那份旧 state 再投一遍。
    # 实测这个缺口真实存在：服务端 33 条 vs 本地 state 28 条，差 5 条
    # （朗斯vs葡萄牙体育、比利亚雷亚尔vs那不勒斯、莱比锡vs埃因霍温、
    #  阿森纳vs里尔、马竞vs曼联）——都是历史上 state 回写失败漏记的。
    #
    # msgId=11 是账号私有的「我已预测」服务端真值（无效 JWT 返回空，
    # 有效 JWT 返回全部）。以它为准，跨机器并发也不会重复下单。
    done = {r.get("match", "") for r in st.get("records", [])}
    _srv = api.my_predictions(jwt)
    if _srv:
        _extra = 0
        for x in _srv:
            m = f"{x.get('home')} vs {x.get('away')}"
            if m not in done:
                _extra += 1
            done.add(m)
        if _extra:
            print(f"challenge: 服务端已预测 {len(_srv)} 条，其中 {_extra} 条本地 state 没记（已并入去重）")
    else:
        # 取不到就退回纯本地 state，并留痕——这时的重复风险要能被看见
        print("challenge: ⚠ 取不到服务端已预测列表，本轮仅按本地 state 去重")

    cands = []
    near = []  # 临时灌仓通道：-0.5pp ≤ edge < 门槛（CHALLENGE_TOPFILL 启用时按 EV 补足）
    for it in items:
        sid, h, a = it.get("sid"), it.get("home"), it.get("away")
        kts = it.get("kick_ts")
        if not sid or not kts or kts < now.timestamp() + 45 * 60:
            continue
        match = f"{h} vs {a}"
        if match in done:
            continue
        p = pred.get((h, a))
        if not p:
            for (ph, pa), pp in pred.items():
                if h and pa and (h in pa or pa in h) and a and pp and (a in ph or ph in a):
                    p = pp
                    break
        if not p:
            continue
        fp = [p.get("home_win_prob"), p.get("draw_prob"), p.get("away_win_prob")]
        mf, odds = p.get("market_fair"), [p.get("home_odds"), p.get("draw_odds"), p.get("away_odds")]
        if None in fp or not mf or not odds[0]:
            continue
        sel = max(range(3), key=lambda i: fp[i])
        o, P = odds[sel], fp[sel]
        if not o or o < 1.60:
            continue
        e = P - mf[sel]
        rec = {"sid": sid, "match": match, "dir": sel, "p": P, "mkt_p": mf[sel],
               "edge": e, "odds": o, "ev": P * 100 * mult(o)}
        if e >= g:
            cands.append(rec)
        elif e >= float(os.environ.get("CHALLENGE_TOPFILL_FLOOR", "-0.005")):
            near.append(rec)
    cands.sort(key=lambda c: -c["ev"])
    K = min(8 if fill else 6, remaining)
    picked = cands[:K]
    try:
        topfill = min(int(os.environ.get("CHALLENGE_TOPFILL", "0") or 0), remaining)
    except ValueError:
        topfill = 0
    if topfill > len(picked):
        have = {c["sid"] for c in picked}
        for c in sorted(near, key=lambda c: -c["ev"]):
            if len(picked) >= topfill:
                break
            if c["sid"] in have:
                continue
            c["fill"] = True
            picked.append(c)
    if not picked:
        print(f"challenge: 零合格（门槛{g:.1%}/池{len(items)}）——正常")
        return 0
    ok_n = 0
    for c in picked:
        if api.submit(jwt, c["sid"], 1, c["dir"] + 1) or api.submit(jwt, c["sid"], 1, c["dir"] + 1):
            ok_n += 1
            st["records"].append({"match": c["match"], "pick": f"{['主胜','平','客胜'][c['dir']]}@{c['odds']:.2f}",
                                  "market": "1x2", "sid": c["sid"], "p": round(c["p"], 4),
                                  "mkt_p": round(c["mkt_p"], 4), "edge": round(c["edge"], 4),
                                  "line": None, "ev": round(c["ev"], 1), "via": "api-ci",
                                  "mode": "临时灌仓1018(实验注)" if c.get("fill") else (st.get("mode") or "normal"),
                                  "submitted_at": now.strftime("%F %T")})
    st["used"] += ok_n
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"challenge: 提交 {ok_n} 注 → {st['used']}/{st['quota']} 门槛{g:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
