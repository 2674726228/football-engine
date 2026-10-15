#!/usr/bin/env python3
"""挑战提交复盘器（2026-10-15）——把 challenge_state.records 变成系统审计数据

每条提交记录（提交时预登记的声明）↔ 账本结算结果 ↔ 收盘市场隐含，产出四张表：
1. 命中校准：声明 P vs 实际命中率（按 edge 分桶 → 验证门槛是否有效）
2. edge 兑现率：提交的 edge vs 实际(赛果−收盘隐含)——我们的分歧到终盘还剩多少
3. 市场对比：让球 vs 胜平负 两池分别统计（让球走比分分布，是独立信息源）
4. 挑战积分流水（若 records 带 points 字段，来自站点结算回读）

用法: challenge_review.py [--week 2026-W42]
"""
import json, sys

ROOT = "."

def load_state():
    return json.load(open(f'{ROOT}/_vault/challenge_state.json'))

def main():
    week = None
    if '--week' in sys.argv:
        week = sys.argv[sys.argv.index('--week') + 1]
    st = load_state()
    if week and st.get('week') != week:
        pass
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
    print(f"=== 挑战复盘 {st['week']}：{n} 注已结算，{hits} 中（{(hits/n if n else 0):.0%}）===")
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

if __name__ == '__main__':
    main()
