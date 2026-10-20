"""社区倾向快照归档的回归测试（E6 观测实验，2026-10-21）。

为什么值得测：这个脚本要每天跑、攒几个月才能回测。它的解析层一旦静默
退化成「全部解析为空」，归档会变成一堆空文件，几个月后才发现白攒——
那时样本窗口已经错过了。所以解析层必须锁死。

背景（用 focus 页 DOM 做真值对齐 8 场后钉死的）：
  msgId=17 → f5 → f4×N，每条 = 一场
    f1 = 比赛信息（sub 3=主队、4=客队，队名在 sub 3 的中文名里）
    f2 = 短 id
    f3 = 预测总人数
    f4 ×M = 分布条目 {f1: 市场内选项序号, f2: 票数, f3: 该市场内占比%}
  不变式：同市场内 f2/f3*100 收敛到同一整数 → 用来聚类市场组。

注意 f1（选项码）**不跨场次同义**——同一数字在不同场次可能属于不同市场，
所以方向名只能来自 DOM，脚本里另做了 DOM 对齐。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "njstats_community_snapshot", ROOT / "scripts" / "njstats_community_snapshot.py"
)
assert SPEC and SPEC.loader
snap = importlib.util.module_from_spec(SPEC)
sys.modules["njstats_community_snapshot"] = snap
SPEC.loader.exec_module(snap)


# ---------------------------------------------------------------- 编解码

def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _fv(fn: int, n: int) -> bytes:
    return _varint(fn << 3) + _varint(n)


def _fs(fn: int, payload: bytes) -> bytes:
    return _varint((fn << 3) | 2) + _varint(len(payload)) + payload


def test_parse_pb_roundtrip_varint_and_bytes():
    blob = _fv(1, 7) + _fs(2, b"hi") + _fv(3, 300)
    got = snap.parse_pb(blob)
    assert (1, "v", 7) in got
    assert (2, "s", b"hi") in got
    assert (3, "v", 300) in got


def test_parse_pb_rejects_unknown_wire_type():
    with pytest.raises(ValueError):
        snap.parse_pb(_varint((1 << 3) | 3))  # wire type 3 不支持


# ---------------------------------------------------------------- 市场聚类

def test_cluster_groups_by_implied_voters():
    """狼队那场的真实数据：码8/9/11/12/13 隐含市场票数都收敛到 4。"""
    dist = [
        {"code": 8, "votes": 3, "pct": 75},
        {"code": 9, "votes": 1, "pct": 25},
        {"code": 11, "votes": 1, "pct": 25},
        {"code": 12, "votes": 2, "pct": 50},
        {"code": 13, "votes": 1, "pct": 25},
    ]
    groups = snap._cluster_markets(dist)
    assert len(groups) == 1, f"应聚成一个市场组，实际 {len(groups)}"
    assert len(groups[0]) == 5


def test_cluster_separates_two_markets():
    """那不勒斯那场：码8/9/10 隐含 5，码11/12/13 隐含 10 → 两组。"""
    dist = [
        {"code": 8, "votes": 3, "pct": 60},
        {"code": 9, "votes": 1, "pct": 20},
        {"code": 10, "votes": 1, "pct": 20},
        {"code": 11, "votes": 1, "pct": 10},
        {"code": 12, "votes": 1, "pct": 10},
        {"code": 13, "votes": 8, "pct": 80},
    ]
    groups = snap._cluster_markets(dist)
    assert len(groups) == 2, f"应聚成两个市场组，实际 {len(groups)}"
    sizes = sorted(len(g) for g in groups)
    assert sizes == [3, 3]


def test_cluster_tolerates_pct_rounding():
    """f3 是整数百分比，隐含票数会有小数抖动，不能被拆成两组。"""
    dist = [
        {"code": 11, "votes": 5, "pct": 71},   # 5/71*100 = 7.04
        {"code": 12, "votes": 2, "pct": 29},   # 2/29*100 = 6.90
    ]
    groups = snap._cluster_markets(dist)
    assert len(groups) == 1


def test_cluster_ignores_zero_pct_and_votes():
    dist = [
        {"code": 1, "votes": 0, "pct": 0},
        {"code": 2, "votes": 3, "pct": 100},
    ]
    groups = snap._cluster_markets(dist)
    assert len(groups) == 1
    assert groups[0][0]["code"] == 2


# ---------------------------------------------------------------- 整体解析

def _build_response(matches: list[dict]) -> bytes:
    """把一个简化场次列表编成 msgId=17 形状的响应。"""
    entries = b""
    for m in matches:
        # f1 比赛信息：3=主队, 4=客队（队名在 sub 3）
        info = _fs(3, _fs(3, m["home"].encode())) + _fs(4, _fs(3, m["away"].encode()))
        info += _fs(6, str(m.get("kick_ts", 1792546200)).encode())
        entry = _fs(1, info) + _fs(2, m["sid"].encode()) + _fv(3, m["people"])
        for code, votes, pct in m["dist"]:
            entry += _fs(4, _fv(1, code) + _fv(2, votes) + _fv(3, pct))
        entries += _fs(4, entry)
    f5 = entries
    return b"\x12\x03suc" + _fs(5, f5)


def test_parse_community_end_to_end():
    raw = _build_response([
        {
            "sid": "ABCd", "home": "狼队", "away": "朴茨茅斯", "people": 12,
            "dist": [(8, 3, 75), (9, 1, 25), (11, 1, 25), (12, 2, 50), (13, 1, 25)],
        },
    ])
    out = snap.parse_community(raw)
    assert len(out) == 1
    it = out[0]
    assert it["sid"] == "ABCd"
    assert it["home"] == "狼队"
    assert it["away"] == "朴茨茅斯"
    assert it["people"] == 12
    assert len(it["distribution"]) == 5
    assert it["distribution"][0]["votes"] == 3
    # top 应落在票数最多（唯一）市场组里、占比最高的那条
    assert it["top"]["pct"] == 75
    assert it["top"]["code"] == 8


def test_parse_community_handles_empty_body():
    assert snap.parse_community(b"") == []
    assert snap.parse_community(b"\x12\x03suc") == []
