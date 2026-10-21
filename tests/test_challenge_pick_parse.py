"""挑战复盘 pick 解析与命中判定的回归测试（2026-10-21）。

## 为什么改

`challenge_review.py` 原来靠子串猜方向：

```python
if '主队-1' in pick or pick.startswith('主胜'): dir_ = 0
elif pick.startswith('平'): dir_ = 1
elif '负@' in pick or pick.startswith('客胜'): dir_ = 2
```

实测 9 种真实格式里 **4 种解析失败且静默返回 None**。`None` 去比
`actual_idx` 永远 False —— **把命中的注算成未中**，成绩被系统性低估。
受影响的真实记录：`胜@2.98`（考文垂）、`胜@3.3`（克拉约瓦）、
`客+0.5@0.95`（霍芬海姆）、`让负(-2)@2.04`（站点格式）。

另一个问题：让球方向不能直接等同胜平负方向。「主队-1」要让 1 球，
比分 1:0 是走盘、2:0 才赢，需要比分才能判。

## 修复后的口径

`parse_pick()` 拆出 {play, dir, line, side}；`judge_hit()` 按 play 分别判：
- 1x2：`actual_idx == dir`
- handicap：用比分 + 让球线算，信息不足返回 **None**（不是 False）

修复后战绩从「24 注 12 中（50%）」变为「26 注 14 中（54%）」——
找回的两注正是之前被静默算错的，且与站点真值一致
（霍芬海姆客+0.5 命中 +195、亚特兰大主队-1 命中 +195）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "challenge_review", ROOT / "scripts" / "challenge_review.py"
)
assert SPEC and SPEC.loader
cr = importlib.util.module_from_spec(SPEC)
sys.modules["challenge_review"] = cr
SPEC.loader.exec_module(cr)


# ---------------------------------------------------------------- parse_pick

@pytest.mark.parametrize("pick,expected_dir", [
    ("主胜@1.65", 0),
    ("胜@2.98", 0),      # 简化写法——旧实现解析失败
    ("胜@3.3", 0),       # 同上
    ("平@3.0", 1),
    ("客胜@2.0", 2),
    ("负@2.05", 2),
])
def test_parse_pick_1x2(pick, expected_dir):
    p = cr.parse_pick(pick)
    assert p["play"] == "1x2"
    assert p["dir"] == expected_dir


@pytest.mark.parametrize("pick,expected_side,expected_line", [
    ("主队-1@0.95", "home", 1.0),
    ("主队-1@0.95（让球实验）", "home", 1.0),
    ("客队+0.5@0.95", "away", 0.5),
    ("客+0.5@0.95", "away", 0.5),      # 旧实现解析失败
    ("让负(-2)@2.04", "away", 2.0),    # 站点格式，旧实现解析失败
    ("让胜(-1)@2.03", "home", 1.0),
])
def test_parse_pick_handicap(pick, expected_side, expected_line):
    p = cr.parse_pick(pick)
    assert p["play"] == "handicap", f"{pick} 应识别为让球"
    assert p["side"] == expected_side
    assert p["line"] == expected_line


def test_parse_pick_unknown_is_explicit():
    """解析不了要显式标 unknown，不能静默当 1x2。"""
    assert cr.parse_pick("随便写的")["play"] == "unknown"
    assert cr.parse_pick("")["play"] == "unknown"


# ---------------------------------------------------------------- judge_hit

def test_judge_1x2():
    assert cr.judge_hit(cr.parse_pick("主胜@2.0"), 0) is True
    assert cr.judge_hit(cr.parse_pick("主胜@2.0"), 1) is False


def test_judge_handicap_home_win_needs_margin():
    """主队-1：净胜 1 球是走盘（不算命中），净胜 2 球才赢。"""
    p = cr.parse_pick("主队-1@0.95")
    assert cr.judge_hit(p, 0, 2, 0) is True    # 净胜 2 → 赢
    assert cr.judge_hit(p, 0, 1, 0) is False   # 净胜 1 → 走盘，不算命中
    assert cr.judge_hit(p, 0, 3, 1) is True    # 净胜 2 → 赢
    assert cr.judge_hit(p, 2, 0, 1) is False   # 输球


def test_judge_handicap_away_plus_half():
    """客+0.5：客队平或胜都算命中。"""
    p = cr.parse_pick("客+0.5@0.95")
    assert cr.judge_hit(p, 2, 0, 1) is True    # 客胜
    assert cr.judge_hit(p, 1, 1, 1) is True    # 平 → 受让半球赢
    assert cr.judge_hit(p, 0, 2, 0) is False   # 主胜


def test_judge_handicap_without_score_returns_none():
    """没有比分时返回 None，不能被当成未中。"""
    p = cr.parse_pick("主队-1@0.95")
    assert cr.judge_hit(p, 0) is None
    assert cr.judge_hit(p, 0, None, None) is None


def test_judge_unknown_returns_none():
    assert cr.judge_hit(cr.parse_pick("随便写的"), 0) is None


# ---------------------------------------------------------------- 回归：真实记录

def test_real_records_previously_misparsed():
    """这四注是旧实现解析失败的真实记录，现在必须能判。"""
    assert cr.parse_pick("胜@2.98")["dir"] == 0
    assert cr.parse_pick("胜@3.3")["dir"] == 0
    assert cr.parse_pick("客+0.5@0.95")["play"] == "handicap"
    assert cr.parse_pick("让负(-2)@2.04")["play"] == "handicap"
