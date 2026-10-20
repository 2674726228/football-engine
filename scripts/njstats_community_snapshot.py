#!/usr/bin/env python3
"""社区倾向快照归档（E6 观测实验，2026-10-21）

把 NJStats「预测聚焦」页的社区预测分布落盘，供日后回测裁决使用。

为什么需要：站点的社区倾向数据**没有历史接口**——`modId=1012/msgId=17`
不接受任何日期参数（实测 f1=日期串、f1={1:日期}、f1={1:天数} 返回的
字节数完全相同），所以想评估它只能自己每天存一份。这是 `docs/
EXPERIMENTS.md` 里 E6 行的观测前提。

数据形状（逆向自 msgId=17，见 _vault/recon_njstats.md）：
  msgId=17 → f5
    ├ f1 ×N   分析师（未用）
    ├ f3 ×N   用户榜（未用）
    └ f4 ×N   场次
         f1 = 比赛信息（sub 2=联赛、3=主队、4=客队、6=开球秒）
         f2 = 短 id（与 msgId=1 板同命名空间）
         f3 = 预测总人数
         f4 ×M = 分布条目 {f1=方向码, f2=市场码, f3=占比%}

实测：多数场次 f4 只有 1 条（单人 100%，零信息量），只有少数场次有
多方向分布。脚本照存不误——样本量得靠日期累积，不能靠挑好看的存。

用法:
    .venv/bin/python3 scripts/njstats_community_snapshot.py           # 抓取并落盘
    .venv/bin/python3 scripts/njstats_community_snapshot.py --check   # 只探测不写

输出: data/state/njstats/community/<YYYY-MM-DD>.json
退出码: 0 正常（含「今天没有社区数据」）；1 抓取失败
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import njstats_api_submit as api  # noqa: E402

OUT_DIR = ROOT / "data" / "state" / "njstats" / "community"
BJT = timezone(timedelta(hours=8))

# 语义已用 focus 页 DOM 做真值对齐 8 场，钉死如下：
#
# 分布条目 = {f1: 条目码, f2: 票数, f3: **该市场内**占比%}
#
# 关键不变式（8/8 场成立）：同一市场内各选项满足
#       f2 / f3 * 100 ≈ 该市场的投票总数（收敛到同一整数）
# 例：狼队那场 码8(3票,75%) 码9(1票,25%) 码11(1票,25%) 码12(2票,50%) 码13(1票,25%)
#     隐含市场票数都是 4 → 这 5 条属同一市场。
#     那不勒斯 码8/9/10 隐含都是 5；码11/12/13 隐含都是 10 → 两个市场。
#
# 因此 **f1 是「市场内选项序号」，其数值不跨场次同义**——同一 f1 在不同
# 场次可能属于不同市场（这是之前拿 f1 对 DOM 一直对不上的原因）。
# 脚本用上面那个不变式自动聚类出市场组，不预设市场含义。
#
# DOM 的「社区倾向 X%」= 所有条目里 **票数最多的那个市场组** 中
# 占比最高的条目（即主流市场的多数意见），X 即其 f3。
# 早期误以为「取全表 pct 最大」，实测 3/8 不符，已否。
DIR_NOTE = "条目码(f1)为该市场内选项序号，不跨场次同义"


def read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def parse_pb(buf: bytes) -> list[tuple[int, str, object]]:
    """严格 protobuf 解析（wire type 0/1/2/5）。

    不用 engine 里那个 decode_msg——它对 wire type 0 与 2 混排的嵌套消息
    会抛 `object of type 'int' has no len()`（社区数据结构里 f1 就踩到了）。
    """
    i = 0
    out: list[tuple[int, str, object]] = []
    while i < len(buf):
        tag, i = read_varint(buf, i)
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            val, i = read_varint(buf, i)
            out.append((fn, "v", val))
        elif wt == 2:
            ln, i = read_varint(buf, i)
            out.append((fn, "s", buf[i:i + ln]))
            i += ln
        elif wt == 5:
            out.append((fn, "f32", int.from_bytes(buf[i:i + 4], "little")))
            i += 4
        elif wt == 1:
            out.append((fn, "f64", int.from_bytes(buf[i:i + 8], "little")))
            i += 8
        else:
            raise ValueError(f"unknown wire type {wt} at offset {i}")
    return out


def _sub(fields, fn: int) -> bytes | None:
    for f, kind, v in fields:
        if f == fn and kind == "s":
            return v
    return None


def _name(team_blob: bytes | None) -> str:
    """队名子消息：优先 f3（中文名），退回 f2（英文名）。"""
    if not team_blob:
        return ""
    try:
        sub = parse_pb(team_blob)
    except ValueError:
        return ""
    for fn in (3, 2):
        b = _sub(sub, fn)
        if b:
            return b.decode("utf-8", "ignore")
    return ""


def _cluster_markets(dist: list[dict], tol: float = 0.06) -> list[list[dict]]:
    """把分布条目聚类成市场组。

    不变式：同一市场内 f2/f3*100 ≈ 该市场总票数（同一整数）。
    这里按这个隐含值排序后做单链聚类（相对容差 tol 吸收 f3 取整误差）。
    """
    items = [x for x in dist if x.get("votes") and x.get("pct")]
    if not items:
        return []

    def implied(x):
        return x["votes"] / x["pct"] * 100.0

    items.sort(key=implied)
    groups: list[list[dict]] = [[items[0]]]
    for prev, cur in zip(items, items[1:]):
        a, b = implied(prev), implied(cur)
        if abs(b - a) <= max(tol * max(abs(a), abs(b)), 0.5):
            groups[-1].append(cur)
        else:
            groups.append([cur])
    return groups


def parse_community(raw: bytes) -> list[dict]:
    """msgId=17 响应 → 社区倾向条目（纯函数，可测）。"""
    idx = raw.find(b"suc")
    body = raw[idx + 3:] if idx >= 0 else raw
    try:
        top = parse_pb(body)
    except ValueError:
        return []
    f5 = _sub(top, 5)
    if not f5:
        return []
    try:
        level1 = parse_pb(f5)
    except ValueError:
        return []

    out: list[dict] = []
    for fn, kind, blob in level1:
        if fn != 4 or kind != "s":
            continue
        try:
            entry = parse_pb(blob)
        except ValueError:
            continue
        d = {f: v for f, k, v in entry}
        sid_b = d.get(2)
        sid = sid_b.decode("utf-8", "ignore") if isinstance(sid_b, bytes) else ""
        if not sid:
            continue
        f1 = d.get(1)
        home = away = league = ""
        kickoff = None
        if isinstance(f1, bytes):
            try:
                info = parse_pb(f1)
            except ValueError:
                info = []
            home = _name(_sub(info, 3))
            away = _name(_sub(info, 4))
            lg = _sub(info, 2)
            if lg:
                try:
                    league = _name(lg) or _sub(parse_pb(lg), 3).decode("utf-8", "ignore")
                except (ValueError, AttributeError):
                    league = ""
            kt = _sub(info, 6)
            if kt:
                try:
                    kickoff = datetime.fromtimestamp(
                        int(kt.decode()), BJT
                    ).strftime("%Y-%m-%d %H:%M")
                except (ValueError, OSError):
                    kickoff = None

        # 分布条目：一条 = {条目码, 票数, 该市场内占比}
        dist = []
        for f, k, v in entry:
            if f != 4 or k != "s":
                continue
            try:
                parts = {a: b for a, _c, b in parse_pb(v)}
            except ValueError:
                continue
            code = parts.get(1)
            if code is None:
                continue
            dist.append({
                "code": code,           # f1：该市场内的选项序号（不跨场次同义）
                "votes": parts.get(2),  # f2：票数
                "pct": parts.get(3),    # f3：该市场内占比%
            })
        # 用「票数/占比×100 收敛到同一整数」把条目聚类成市场组
        groups = _cluster_markets(dist)
        # DOM 口径：票数最多的市场组里，占比最高的那条
        top = None
        if groups:
            main = max(groups, key=lambda g: sum(x["votes"] or 0 for x in g))
            top = max(main, key=lambda x: (x["pct"] or 0), default=None)

        people = d.get(3)
        out.append({
            "sid": sid,
            "home": home,
            "away": away,
            "league": league,
            "kickoff": kickoff,
            "people": people if isinstance(people, int) else 0,
            "top": top,          # DOM 口径的「社区倾向」条目
            "distribution": dist,
        })
    return out


def fetch() -> list[dict] | None:
    jwt = api.JWT_FILE.read_text().strip() if api.JWT_FILE.exists() else ""
    if not jwt:
        print("community: 无 JWT，跳过", file=sys.stderr)
        return None
    st, raw = api._post_jwt(jwt, "1012", "17", api._field(3, b""))
    if st != 200:
        print(f"community: HTTP {st}", file=sys.stderr)
        return None
    return parse_community(raw)


def fetch_dom_labels() -> dict[tuple[str, str], dict]:
    """从 focus 页 DOM 抓「社区倾向 主胜 75%」这类**方向真值**。

    API 的 f1 码只在「该市场内选项序号」这个层面有确定性，不含方向名称
    （同一 f1 值在不同场次可属不同市场）。站点 DOM 是唯一能给出
    「主胜/平/客胜」字面值的地方，所以方向必须靠它。
    返回 {(主队, 客队): {dir: '主胜', pct: 75}}
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("community: 无 playwright，跳过 DOM 标签", file=sys.stderr)
        return {}

    profile = Path.home() / ".football-engine" / "pwprofile"
    if not profile.exists():
        print("community: 无 pwprofile，跳过 DOM 标签", file=sys.stderr)
        return {}

    out: dict[tuple[str, str], dict] = {}
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile), headless=True, viewport={"width": 1400, "height": 1000}
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.njstats.cn/prediction/focus",
                      wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(5000)
            rows = page.query_selector_all(".focus-match-row")
            for row in rows:
                try:
                    text = (row.inner_text() or "").replace("\n", " ")
                except Exception:
                    continue
                mm = re.search(r"([^\s|]+)\s+VS\s+([^\s|]+)", text)
                cm = re.search(r"社区倾向\s*(\S+?)\s*(\d+)%", text)
                if mm and cm:
                    out[(mm.group(1), mm.group(2))] = {
                        "dir": cm.group(1), "pct": int(cm.group(2)),
                    }
        finally:
            ctx.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="社区倾向快照归档")
    ap.add_argument("--check", action="store_true", help="只探测不写文件")
    args = ap.parse_args(argv)

    items = fetch()
    if items is None:
        return 1

    # 只保有真实分布的；单人单条目 = 零信息量，不落盘（但计入 total_matches）
    informative = [
        it for it in items
        if len(it["distribution"]) > 1 or it["people"] > 1
    ]
    print(f"community: 总场次 {len(items)}，有分布信号 {len(informative)}")

    if args.check:
        for it in informative:
            top = it.get("top") or {}
            print(f"  {it['home']} vs {it['away']}  人数={it['people']}  "
                  f"倾向=码{top.get('code')} {top.get('pct')}%  条目数={len(it['distribution'])}")
        return 0

    # 方向真值只存在于 DOM，逐场对齐贴上
    labels = fetch_dom_labels()
    matched = 0
    for it in informative:
        lab = labels.get((it["home"], it["away"]))
        if lab is None:
            # 队名可能有细微差异（全称/简称），退一步按单侧包含匹配
            for (h, a), v in labels.items():
                if (h and h in it["home"]) or (it["home"] and it["home"] in h):
                    if (a and a in it["away"]) or (it["away"] and it["away"] in a):
                        lab = v
                        break
        it["dom_tendency"] = lab
        if lab:
            matched += 1
    print(f"community: DOM 方向标签命中 {matched}/{len(informative)}")

    day = datetime.now(BJT).strftime("%Y-%m-%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{day}.json"
    payload = {
        "snapshot_at": datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S"),
        "total_matches": len(items),
        "informative_matches": len(informative),
        "dom_labeled": matched,
        "schema_note": (
            "distribution[]: {code: 市场内选项序号(不跨场次同义), votes: 票数, "
            "pct: 该市场内占比%}; top = 票数最多市场组里占比最高的条目; "
            "dom_tendency = {dir: 主胜/平/客胜, pct} 来自站点 DOM，是唯一可信的方向真值"
        ),
        "matches": informative,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
