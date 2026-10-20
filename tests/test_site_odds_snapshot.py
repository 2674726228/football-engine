"""站点赔率解析 + 快照的回归测试（2026-10-21）。

背景：本地算 edge 用 `predictions.json` 的**体彩官方**赔率，而挑战赛按
**NJStats 站点赔率**结算，实测前者系统性低估约 10%（18 条里 17 条低估，
比值均值 1.0993，见 docs/CHALLENGE_ODDS_GAP_20261021.md）。

要验证「换成站点赔率算 EV 能不能提高命中」，必须先把站点赔率逐日存下来
——它只在当前板上可见，赛后就变了。所以 `njstats_duty` 每天落一份
`data/state/njstats/site_odds/<date>.json`。

这道测试锁住解析层：msgId=1 的赔率包一旦结构变了，快照会静默变成
一堆 `odds: null`，几个月后回测才发现白攒。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

api = importlib.import_module("njstats_api_submit")


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


def _build_board(matches: list[dict]) -> bytes:
    """编一个 msgId=1 形状的响应。"""
    entries = b""
    for m in matches:
        info = _fs(3, _fs(3, m["home"].encode())) + _fs(4, _fs(3, m["away"].encode()))
        info += _fs(6, str(m.get("kick_ts", 1792531800)).encode())
        entry = _fs(1, info)
        if m.get("odds"):
            # 真实结构（抓包确认）：f2.f1 下三个子字段 1/2/3，各直接是赔率串
            trio = b""
            for i, o in enumerate(m["odds"], start=1):
                trio += _fs(i, str(o).encode())
            entry += _fs(2, _fs(1, trio))
        entry += _fs(7, m["sid"].encode())
        entries += _fs(1, entry)
    return b"\x12\x03suc" + _fs(5, entries)


def test_parse_board_with_odds_reads_trio():
    raw = _build_board([
        {"sid": "K3Mj", "home": "佩雷拉", "away": "茨高", "odds": [2.3, 3.0, 3.4]},
    ])
    out = api.parse_board_with_odds(raw)
    assert len(out) == 1
    assert out[0]["sid"] == "K3Mj"
    assert out[0]["home"] == "佩雷拉"
    assert out[0]["away"] == "茨高"
    assert out[0]["odds"] == [2.3, 3.0, 3.4]


def test_parse_board_with_odds_tolerates_missing_odds():
    """没赔率的场次仍要出现（赔率置 None），不能整条丢掉。"""
    raw = _build_board([{"sid": "AAAa", "home": "甲", "away": "乙"}])
    out = api.parse_board_with_odds(raw)
    assert len(out) == 1
    assert out[0]["odds"] is None


def test_parse_board_with_odds_rejects_bogus_odds():
    """赔率里出现 <=1 的值要整体判为无效（别把让球水位 0.95 当 1x2 赔率）。"""
    raw = _build_board([
        {"sid": "BBBb", "home": "丙", "away": "丁", "odds": [0.95, 3.0, 3.4]},
    ])
    out = api.parse_board_with_odds(raw)
    assert out[0]["odds"] is None


def test_parse_board_with_odds_empty_body():
    assert api.parse_board_with_odds(b"") == []
    assert api.parse_board_with_odds(b"\x12\x03suc") == []


def test_parse_board_still_works():
    """老函数不能被改坏——challenge_hourly 提交链还在用它。"""
    raw = _build_board([{"sid": "CCCc", "home": "戊", "away": "己", "odds": [2.0, 3.2, 3.8]}])
    out = api.parse_board(raw)
    assert len(out) == 1
    assert out[0]["sid"] == "CCCc"
    assert "odds" not in out[0]
