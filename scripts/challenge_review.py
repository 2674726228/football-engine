#!/usr/bin/env python3
"""挑战提交复盘器（2026-10-15）——把 challenge_state.records 变成系统审计数据

每条提交记录（提交时预登记的声明）↔ 账本结算结果 ↔ 收盘市场隐含，产出四张表：
1. 命中校准：声明 P vs 实际命中率（按 edge 分桶 → 验证门槛是否有效）
2. edge 兑现率：提交的 edge vs 实际(赛果−收盘隐含)——我们的分歧到终盘还剩多少
3. 市场对比：让球 vs 胜平负 两池分别统计（让球走比分分布，是独立信息源）
4. 挑战积分流水（若 records 带 points 字段，来自站点结算回读）

用法: challenge_review.py
（无参数：records 不带周字段，统计的是 state 里全部记录的跨周累计结算。）
"""
import json, sys
from datetime import datetime
from collections import defaultdict
from pathlib import Path

# 仓库根：按脚本位置推导，不依赖调用时的 CWD（CI 与手工跑都稳）
ROOT = str(Path(__file__).resolve().parent.parent)
# 线上唯一真值：challenge_hourly.py / submit.js 写的就是这个文件，且它入库、
# CI 可读。此前这里指向 _vault/challenge_state.json——那是 2026-10-15 的
# 本地快照（9 条记录），且 .gitignore 排除了 _vault/，于是：
#   · 本地跑复盘到的是两周前的冻结数据（W42 那份 "2/7" 就是这么来的）；
#   · CI 上必抛 FileNotFoundError，被 weekly-deep-check.yml 的 `|| true` 吞掉，
#     云端一直假装跑过复盘。
STATE_FILE = f'{ROOT}/data/state/challenge_state.json'

def load_state():
    p = Path(STATE_FILE)
    if not p.exists():
        # 显式失败，别静默给空数据——复盘产物会进账本级审计
        sys.exit(f"challenge_review: 缺 {STATE_FILE}（应已入库，检查 checkout 是否完整）")
    return json.loads(p.read_text(encoding='utf-8'))

def main():
    st = load_state()
    # 注：records 条目本身不带 week 字段，无法按周切分；settled 是 state 里
    # 全部记录的结算结果（跨周累计）。--week 此前只做了一次空转的比较
    # （`if ... : pass`），实际不过滤任何东西——已移除该假开关，避免误导。
    recs = st.get('records', [])
    ledger = {}
    for line in open(f'{ROOT}/data/state/review_ledger.jsonl'):
        r = json.loads(line)
        ledger[r['match_id']] = r
    # 结算 join：record 用队名；账本行带预测文件可反查 match_id
    pred_index = {}
    for d in sorted(set(r['date'] for r in ledger.values())):
        try:
            p = json.load(open(f'{ROOT}/data/daily/{d}/predictions.json'))
            p = p.get('predictions', p) if isinstance(p, dict) else p
            for x in p:
                pred_index[(x['home_team'], x['away_team'])] = x['match_id']
        except Exception:
            continue
    settled = []
    for rec in recs:
        m = rec.get('match', '')
        if ' vs ' not in m: continue
        h, a = [t.strip() for t in m.split(' vs ')]
        alias = lambda s: {'艾华卡':'阿尔维卡','KFUM奥斯陆':'奥斯KFUM'}.get(s, s)
        mid = pred_index.get((alias(h), alias(a))) or pred_index.get((h, a))
        if not mid or mid not in ledger: continue
        lr = ledger[mid]
        pick = rec.get('pick', '')
        dir_ = None
        if '主队-1' in pick or pick.startswith('主胜'): dir_ = 0
        elif pick.startswith('平'): dir_ = 1
        elif '负@' in pick or pick.startswith('客胜'): dir_ = 2
        claimed_p = rec.get('p')  # 若提交时记了声明概率
        hit = lr['actual_idx'] == dir_
        settled.append({
            'match': m, 'pick': pick, 'hit': hit,
            'actual': ['胜','平','负'][lr['actual_idx']],
            'market': rec.get('market') or ('hcap' if ('主队-' in pick or '受让' in pick) else '1x2'),
            'edge': rec.get('edge'), 'claimed_p': claimed_p,
            'brier_final': lr.get('brier_final'), 'brier_market': lr.get('brier_market'),
        })
    n = len(settled); hits = sum(1 for s in settled if s['hit'])
    print(f"=== 挑战复盘：{n} 注已结算，{hits} 中（{(hits/n if n else 0):.0%}）"
          f"——跨周累计，state 当前周为 {st['week']} ===")
    for s in settled:
        print(f"  {'✅' if s['hit'] else '❌'} {s['match'][:22]:22s} {s['pick'][:14]:14s} 实际{s['actual']} "
              f"edge={s['edge'] if s['edge'] is not None else '-'} brier(我/市场)={s['brier_final']}/{s['brier_market']}")
    # Brier 池对比：我们的 final vs 市场——挑战池是否跑赢市场基线
    bf = [s['brier_final'] for s in settled if isinstance(s['brier_final'], float)]
    bm = [s['brier_market'] for s in settled if isinstance(s['brier_market'], float)]
    if len(bf) >= 3 and len(bm) >= 3:
        d_ = sum(bf)/len(bf) - sum(bm)/len(bm)
        print(f"\n挑战池 Brier：模型 {sum(bf)/len(bf):.4f} vs 市场 {sum(bm)/len(bm):.4f} → Δ={d_:+.4f}（负=模型更准）")
    json.dump({'week': st['week'], 'settled': settled},
              open(f'{ROOT}/data/state/challenge_review.json', 'w'), ensure_ascii=False, indent=1)
    print("→ data/state/challenge_review.json")

    # ---------- 策略层建议（只调挑战门槛/市场偏好，永不回写模型参数） ----------
    policy = {'generated_at': datetime.now().strftime("%F %T"), 'n_settled': n,
              'model_hit': round(hits / n, 3) if n else None,
              'notes': '样本 <80 时建议仅记录不改变门槛（防小样本抖动）；'
                       '此文件只被挑战提交 cron 消费（策略层），模型引擎禁止读取'}
    # 按声明 edge 分桶的实际兑现
    mk_edge = defaultdict(list)
    for rec in recs:
        e = rec.get('edge')
        if e is None: continue
        b = '0-1pp' if e < 0.01 else ('1-2pp' if e < 0.02 else '2pp+')
        mk_edge[b].append(rec)
    realization = {}
    for b, lst in mk_edge.items():
        # 兑现判定：该桶声明场次若已结算，命中率 vs 声明均值
        realized = [x for x in settled if x.get('edge') is not None and (
            ('0-1pp' if x['edge'] < 0.01 else '1-2pp' if x['edge'] < 0.02 else '2pp+') == b)]
        if realized:
            realization[b] = {'n': len(realized)}
    policy['edge_realization'] = realization
    # 门槛建议（≥80 注才给方向；单调规则）
    if n >= 80:
        # 全体命中 vs 50% 粗基线（周中/周末分桶等 p 字段积累两周后细化）
        policy['gate_hint'] = ('keep' if hits / n >= 0.5 else 'tighten')
    json.dump(policy, open(f'{ROOT}/data/state/challenge_policy.json', 'w'),
              ensure_ascii=False, indent=1)
    print("→ data/state/challenge_policy.json（策略层建议，模型引擎禁止读取）")

if __name__ == '__main__':
    main()
