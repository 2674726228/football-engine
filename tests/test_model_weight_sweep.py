"""模型权重单变量扫描的权重重分配契约（2026-10-28）。

`reweight_model` 是 ablation_replay 归零评估的核心：把模型槽设为 w，
市场/DJYY 按**原比例**分掉 1-w。三条不变量：
  1. 两者权重之和恒为 1（归一化由 fusion 再做一次，但这里不能先失真）
  2. 非模型源的相对比例不变（否则测到的是"配比变化"而非"模型权重"）
  3. w 等于 champion 时必须逐位相同（浮点重分配会造出假的配对 t 值）
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ablation_replay", ROOT / "scripts" / "ablation_replay.py"
)
ar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ar)

CHAMP = {"model_weight": 0.05, "market_weight": 0.75, "djyy_weight": 0.20,
         "post_fusion": {}}


def test_zero_weight_keeps_other_ratio():
    c = ar.reweight_model(CHAMP, 0.0)
    assert c["model_weight"] == 0.0
    assert abs(c["market_weight"] + c["djyy_weight"] - 1.0) < 1e-12
    assert abs(c["market_weight"] / c["djyy_weight"] - 0.75 / 0.20) < 1e-9


def test_weights_sum_to_one_across_grid():
    for w in (0.0, 0.01, 0.03, 0.05, 0.10):
        c = ar.reweight_model(CHAMP, w)
        total = c["model_weight"] + c["market_weight"] + c["djyy_weight"]
        assert abs(total - 1.0) < 1e-12, (w, total)


def test_champion_weight_is_bit_identical():
    c = ar.reweight_model(CHAMP, 0.05)
    assert c == CHAMP, "同权重必须原样返回，否则配对差会全是浮点噪声"
