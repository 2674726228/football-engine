#!/usr/bin/env python3
"""评测层（judge panel）注册表 —— 与输入层同构的多源冗余

原则（docs/EVAL_PLANE.md）：
- 喂模型的（账本/CLV/收盘价）与评模型的（各裁判产物）是两条独立管道，各自多源
- 任何裁判失联：只降级、不崩溃、不触碰预测核心
- 新裁判接入=实现下方 REPORT 约定的一个 JSON，registry 自动识别

裁判席（W43 编制）:
  ledger_clv   主审·永久   全量账本 + CLV（clv_report.json）
  tracker      主审·永久   配对 Brier vs 自身基线（upgrade_tracker.json）
  edge_calib   陪审·永久   分桶 EV 兑现（edge_calibration.json）
  aut_paper    陪审·永久   AUT 平局纸面证据（paper_track_draw.json）
  challenge    陪审·临时   预登记池（challenge_review.json，站点下线即退席）
  djyy_panel   外援·临时   DJYY 门户 120k 回测引擎（匿名可用，协议变即断）
输出: data/state/eval_registry.json（每周深度体检读取并简报"评委席状态"）
"""
import json
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ST = ROOT / "data" / "state"

JUDGES = {
    'ledger_clv':  dict(file='clv_report.json',      kind='permanent',
                        samples=lambda d: d.get('overall', {}).get('n', 0),
                        verdict=lambda d: d.get('overall', {}).get('verdict')),
    'tracker':     dict(file='upgrade_tracker.json', kind='permanent',
                        samples=lambda d: d.get('v2_clean', {}).get('n', 0),
                        verdict=lambda d: f"{d.get('verdict')}/{d.get('verdict_vs_market','?')}"),
    'edge_calib':  dict(file='edge_calibration.json', kind='permanent',
                        samples=lambda d: d.get('n_settled', 0),
                        verdict=lambda d: None),
    'aut_paper':   dict(file='paper_track_draw.json', kind='permanent',
                        samples=lambda d: d.get('n', 0),
                        verdict=lambda d: f"ROI {d.get('roi')}"),
    'challenge':   dict(file='challenge_review.json', kind='temporary',
                        samples=lambda d: len(d.get('settled', [])),
                        verdict=lambda d: f"{sum(1 for s in d.get('settled', []) if s['hit'])}/{len(d.get('settled', []))}"),
    'djyy_panel':  dict(file='djyy_audit_latest.json', kind='temporary',
                        samples=lambda d: d.get('matched', 0),
                        verdict=lambda d: None),
}

STALE_DAYS = {'permanent': 10, 'temporary': 4}   # permanent 文件 10 天没更新=主审打盹；临时裁判宽限短

def main():
    judges, live, dead = {}, 0, 0
    for jid, spec in JUDGES.items():
        f = ST / spec['file']
        entry = {'kind': spec['kind'], 'file': spec['file']}
        try:
            if not f.exists():
                raise FileNotFoundError
            age = (time.time() - f.stat().st_mtime) / 86400
            data = json.loads(f.read_text(encoding='utf-8'))
            entry.update(status='live' if age <= STALE_DAYS[spec['kind']] else 'stale',
                         age_days=round(age, 1),
                         samples=spec['samples'](data),
                         verdict=spec['verdict'](data))
        except Exception as e:
            entry.update(status='offline', age_days=None, samples=0,
                         verdict=None, note=type(e).__name__)
        live += entry['status'] == 'live'
        dead += entry['status'] == 'offline'
        judges[jid] = entry
    # 合议结论：主审们（permanent 且 live）的模型vs市场方向一致性
    quorums = []
    try:
        tr = json.loads((ST / 'upgrade_tracker.json').read_text(encoding="utf-8"))
        if tr.get('paired_vs_market'):
            pm = tr['paired_vs_market']
            quorums.append({'judge': 'tracker', 'delta': pm.get('delta'), 'significant': (pm.get('t') or 0) >= 1.96})
    except Exception:
        pass
    try:
        cr = json.loads((ST / 'clv_report.json').read_text(encoding="utf-8"))
        o = cr.get('overall', {})
        quorums.append({'judge': 'ledger_clv', 'mean_clv': o.get('clv_fair_mean'),
                        'significant': abs(o.get('clv_fair_t') or 0) >= 1.96})
    except Exception:
        pass
    out = {'generated_at': datetime.now().strftime('%F %T'),
           'panel': {'total': len(JUDGES), 'live': live, 'offline': dead,
                     'doctrine': '评模型的与喂模型的是两条独立管道，各自多源冗余；任何单席失联只降级不崩溃'},
           'judges': judges, 'quorums': quorums}
    (ST / 'eval_registry.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out['panel'] | {'detail': {k: v['status'] for k, v in judges.items()}},
                     ensure_ascii=False))

if __name__ == '__main__':
    main()
