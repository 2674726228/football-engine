"""DJYY 快照补位 fusion 第三通道的接线测试（2026-10-22）。

实时 DJYY 在 CI 侧被上游整体挑战（403），但仓库快照由本机 duty 抓取并提交。
快照里的 `djyy_model_prob` 必须能在实时源缺失时补进融合，否则 `djyy_weight`
继续被 fusion 重新归一化掉——第三通道名义存在、实际为零。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.sources.djyy_ssr import DJYYSSRSource  # noqa: E402


def test_snapshot_extract_exposes_model_probs():
    src = DJYYSSRSource(Path("/nonexistent"))
    out = src._extract_enrichment({
        "djyy_model_prob": {"home": 0.51, "draw": 0.27, "away": 0.22},
    })
    assert out["djyy_model_prob"] == {"home": 0.51, "draw": 0.27, "away": 0.22}


def test_snapshot_extract_rejects_empty_probs():
    src = DJYYSSRSource(Path("/nonexistent"))
    assert "djyy_model_prob" not in src._extract_enrichment(
        {"djyy_model_prob": {"home": None, "draw": None, "away": None}})


def test_main_uses_snapshot_before_fusion():
    """补位必须发生在 fuse_probabilities 之前；晚一步就还是零贡献。"""
    src = (ROOT / "engine" / "main.py").read_text(encoding="utf-8")
    enrich_at = src.index("_djyy_ssr = djyy_ssr.enrich_prediction")
    fallback_at = src.index('if not djyy_probs and _djyy_ssr.get("djyy_model_prob")')
    fusion_at = src.index("_fusion_out = fuse_probabilities")
    assert enrich_at < fallback_at < fusion_at, \
        "快照补位没有排在融合之前，djyy_probs 仍是 None"
    assert "djyy_ssr_probs += 1" in src, "补位次数没被计数，日志无法区分实时源与快照"
