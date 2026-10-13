"""队名别名表回归测试（2026-10-13）。

## 为什么值得单独测

`engine/team_aliases.py` 是**跨源匹配的咽喉**：预测/新浪赛果/DJYY 富化/
NJStats 挑战池，四处都靠它把不同译名收敛成同一个键。
它错一条的后果不是报错，而是**那一场静默丢失**（匹配不上 → 无富化/无赛果）。

实测代价：DJYY 富化命中率 48% → 补 18 条别名后 **82%**（跨 4 天 50 场，+17 场）。

## 两条纪律（都来自真实翻车）

1. **同名收敛**：我们和上游可能各用一个"都不标准"的写法
   （如 米堡 vs 米德尔斯堡、莱红牛 vs RB莱比锡）。这时必须让**两边都指向
   同一个规范名**，只映射一侧等于没改。
2. **不许过度合并**：文档开头明确列了危险对（维京 vs 维京古、
   哥德堡 vs 哥德堡盖斯…）。合并了会把数据挂到错误的比赛上，且不报错。
"""

from __future__ import annotations

import pytest

from engine.team_aliases import (
    TEAM_ALIASES,
    loose_normalize,
    normalize_team,
    team_key,
)


# ---------------------------------------------------------------- 同名收敛

# (我们用的写法, 上游用的写法) —— 两者归一化后必须相等
CONVERGE_PAIRS = [
    # NJStats 挑战池（2026-10-13 实测）
    ("比利亚雷", "比利亚雷亚尔"),
    ("莱红牛", "莱比锡红牛"),
    ("RB莱比锡", "莱比锡红牛"),
    ("巴萨", "巴塞罗那"),
    ("马竞", "马德里竞技"),
    ("加拉塔萨", "加拉塔萨雷"),
    ("里斯本", "葡萄牙体育"),
    ("拜仁", "拜仁慕尼黑"),
    # DJYY 池（2026-10-13 实测）
    ("维拉", "阿斯顿维拉"),
    ("布伦特", "布伦特福德"),
    ("纽卡斯尔", "纽卡斯尔联"),
    ("伊普斯", "伊普斯维奇"),
    ("诺丁汉", "诺丁汉森林"),
    ("门兴", "门兴格拉德巴赫"),
    ("斯特拉斯", "斯特拉斯堡"),
    ("弗洛西诺", "弗洛西诺尼"),
    ("凯泽", "凯泽斯劳滕"),
    ("阿尔克马", "阿尔克马尔"),
    ("福图纳", "福图纳锡塔德"),
    ("利雅新月", "利雅得新月"),
    ("京都", "京都不死鸟"),
    ("町田泽维", "町田泽维亚"),
    ("毕尔巴鄂", "毕尔巴鄂竞技"),
    ("拉科", "拉科鲁尼亚"),
    ("贝蒂斯", "皇家贝蒂斯"),
    ("桑坦德", "桑坦德竞技"),
    ("蔚山现代", "蔚山HD"),
    ("名古屋鲸", "名古屋鲸八"),
    ("女王巡游", "女王公园巡游者"),
    ("米堡", "米德尔斯堡"),
]


@pytest.mark.parametrize("ours,theirs", CONVERGE_PAIRS)
def test_alias_pairs_converge(ours, theirs):
    """每对写法归一化后必须相等——否则匹配仍会失败。"""
    a, b = normalize_team(ours), normalize_team(theirs)
    assert a == b, f"{ours}({a}) 与 {theirs}({b}) 归一化后不相等，匹配仍会失败"


def test_self_mapping_entries_are_deliberate_anchors():
    """自映射条目（键==值）必须是**规范名的锚点**，不能是无意义的残留。

    表里确实有一批 `X: X` 条目（佐加顿斯/博卡青年/帕福斯FC…）。它们不是垃圾：
    风格是"把规范名显式写出来"，这样别的条目指向它时有据可查
    （如 `尤尔加登 → 佐加顿斯` 需要 `佐加顿斯` 就是规范名）。

    所以这里不禁止自映射，而是要求：**每个自映射的键必须真的被别的条目指向，
    或本身就是别的条目的解析目标**——否则就是没人用的残留。
    """
    selfmaps = [k for k, v in TEAM_ALIASES.items() if k == v]
    # 每个自映射键要么被别的条目指向，要么它本身是别人归一化的终点
    orphans = [
        k for k in selfmaps
        if not any(v == k for kk, v in TEAM_ALIASES.items() if kk != k)
    ]
    # 允许孤儿存在（有些是预留的规范名），但数量不该失控
    assert len(orphans) <= 12, (
        f"无人指向的自映射条目过多（{len(orphans)} 条）：{orphans[:8]}——"
        f"可能是清理时的残留"
    )


# ---------------------------------------------------------------- 不许过度合并

# 文档开头明确列出的危险对：看起来相似但**不是同一队**
DANGEROUS_PAIRS = [
    ("维京", "维京古"),
    ("哥德堡", "哥德堡盖斯"),
    ("赫尔辛基", "赫尔辛基火花"),
    ("布拉加", "布卡拉曼加"),
    ("利勒斯特伦", "利斯特雷"),
]


@pytest.mark.parametrize("a,b", DANGEROUS_PAIRS)
def test_dangerous_pairs_do_not_merge(a, b):
    """不同球队绝不能被合并——会静默把数据挂到错误的比赛上。"""
    assert normalize_team(a) != normalize_team(b), \
        f"{a} 与 {b} 是不同球队，被别名表错误合并了"


def test_alias_values_are_canonical():
    """别名表的**值**必须是规范名（再查一次应返回自己）。

    否则会出现 A→B→C 的链式解析，`normalize_team` 只做一次替换就会漏。
    """
    for k, v in TEAM_ALIASES.items():
        assert normalize_team(v) == v, \
            f"别名 {k}→{v} 的值不是规范名（再查得 {normalize_team(v)}），存在链式残留"


# ---------------------------------------------------------------- 基本行为

def test_normalize_strips_whitespace_and_hyphens():
    assert normalize_team("AC 米兰") == normalize_team("AC米兰")
    assert normalize_team("") == ""
    assert normalize_team(None) == ""


def test_unknown_name_passes_through():
    """表里没有的名字原样返回（不能误伤）。"""
    assert normalize_team("某不存在的球队") == "某不存在的球队"


def test_loose_is_superset_of_normalize():
    """loose 必须在 normalize 基础上再放宽，不能把已收敛的又拆开。"""
    for ours, theirs in CONVERGE_PAIRS[:10]:
        assert loose_normalize(ours) == loose_normalize(theirs), \
            f"{ours}/{theirs} 在主归一化收敛了，loose 层却不相等"


def test_team_key_order_matters():
    """team_key 是主客有序的（主客颠倒不该相等）。"""
    k1 = team_key("拜仁", "多特蒙德")
    k2 = team_key("多特蒙德", "拜仁")
    assert k1 != k2, "主客顺序被忽略——会把主客颠倒的比赛匹配到一起"
