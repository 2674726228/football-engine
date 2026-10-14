#!/usr/bin/env python3
"""NJStats 试用窗口归档导出（2026-10-13 新增）

## 为什么需要这个

NJStats 试用 2026-10-15 00:08 到期。到期后：
- 概率端点的历史页**永久不可得**（数据窗口限最近 3 天）
- 球队统计/开盘赔率的逐日快照**不会再有**

但原始归档（`data/state/njstats_archive/`，实测 33MB+）已按 `.gitignore`
政策移出 git——它是原始可重抓数据，写进 git 会让每次 clone 多背 30MB，
而仓库已经有 68MB 同类数据（`fdc` 35M + archive 33M）。

所以要在**到期前**把它导出到一个 Git 之外、但能长期存放的地方。

## 导出到哪

优先级（按可用性自动选）：

1. `--out <dir>` 指定目录（推荐：移动硬盘 / iCloud / NAS）
   实测本机 `/Volumes/Untitled` 是用户的外置盘，常用作交付介质
2. 默认 `~/football-engine-archive/`（本机 home，不随仓库走）
3. `--tar` 同时打一个 tar.gz 便于整体搬运

**不导出到仓库内**——那会把刚清理的体积又塞回去。

## 用法

    python scripts/njstats_export.py                     # 导出到默认位置
    python scripts/njstats_export.py --out /Volumes/Untitled/njstats
    python scripts/njstats_export.py --tar               # 额外打包
    python scripts/njstats_export.py --verify            # 只校验不导出

## 导出物

    <out>/2026-10-13/
      archive/           # 原始 prob/match/odds JSON（按 .gitignore 政策不入 git）
      features/          # 派生的模型特征（小，同时也在 git 里）
      MANIFEST.json      # 文件数/字节数/覆盖度/校验和
      README.md          # 这份数据是什么、怎么用、到期意味着什么
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 归档/特征目录可用环境变量覆盖。默认目录按 .gitignore 政策不入 git
# （33MB+ 原始可重抓数据），所以 fresh clone 与 CI 上它不存在——覆盖
# 能力让导出脚本能对任意归档位置工作，也让测试可以给子进程传一个隔离
# 的目录。这一点是必需的：测试用 monkeypatch 改的是父进程的模块全局，
# 而 subprocess 会重新 import 模块、只认环境变量，不认 monkeypatch。
ARCHIVE = Path(os.environ.get(
    "NJSTATS_ARCHIVE_DIR",
    str(ROOT / "data" / "state" / "njstats_archive"),
))
FEATURES = Path(os.environ.get(
    "NJSTATS_FEATURES_DIR",
    str(ROOT / "data" / "state" / "njstats_features"),
))
DEFAULT_OUT = Path.home() / "football-engine-archive"

PROB_ENDPOINTS = ["draw", "over", "under", "btts", "home", "away", "ht-goal", "avg-goal"]
EXPECTED_PROB_PAGES = 6      # 实测 maxPage=6（total=571, pageSize=100）


def sha256(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()[:16]


def coverage() -> dict:
    """归档覆盖度体检：哪些缺口会在到期后永久无法补上。

    这是导出的核心价值——不只是"拷文件"，而是**明确告诉你缺什么**，
    这样到期前还能抢。
    """
    prob_dir = ARCHIVE
    gaps = []
    have = {}
    for f in prob_dir.glob("*_prob_*.json"):
        name = f.stem
        if len(name) < 10:
            continue
        date, kind = name[:10], name[11:]
        have.setdefault(date, set()).add(kind)

    for date in sorted(have):
        for ep in PROB_ENDPOINTS:
            pages = sum(
                1 for p in range(1, EXPECTED_PROB_PAGES + 1)
                if (prob_dir / f"{date}_prob_{ep}{'' if p == 1 else f'_p{p}'}.json").exists()
            )
            if pages < EXPECTED_PROB_PAGES:
                gaps.append({
                    "date": date, "endpoint": ep,
                    "have_pages": pages, "need_pages": EXPECTED_PROB_PAGES,
                    "missing_records": (EXPECTED_PROB_PAGES - pages) * 100,
                })

    snaps = sorted(prob_dir.glob("*_snapshot_*.json"))
    return {
        "dates": sorted(have),
        "prob_gaps": gaps,
        "snapshot_files": len(snaps),
        "n_archive_files": len(list(prob_dir.glob("*.json"))),
    }


def build_readme(cov: dict, stamp: str) -> str:
    gap_lines = "\n".join(
        f"| {g['date']} | {g['endpoint']} | {g['have_pages']}/{g['need_pages']} | ~{g['missing_records']} 条 |"
        for g in cov["prob_gaps"]
    ) or "| — | — | 无缺口 | 0 |"
    return f"""# NJStats 试用窗口归档（导出于 {stamp}）

## 这是什么

NJStats（鸟叫足球网）试用会员期间抓取的 API 归档。**试用 2026-10-15 00:08 到期**，
到期后概率端点的历史页永久不可得（数据窗口限最近 3 天）。

## 目录

| 路径 | 内容 | 是否在 git 里 |
|---|---|---|
| `archive/` | 原始 API JSON（prob 8 端点 × 分页 + match/odds + team） | ❌ 按政策不入 git（原始可重抓数据） |
| `features/` | 派生的模型特征（开盘赔率去水 / 主客分离统计） | ✅ 也在 `data/state/njstats_features/` |

## 数据内容

- **`prob_*`**：每场含主客队 **overall/home/away 三套完整统计**（ppg/进失球/半场/
  胜负平/领先落后 各 20+ 字段）+ **openingHome/Draw/Away 开盘赔率** + drawRate
- **`match`/`odds`**：逐日赛程与多庄指数（CLV 基准）
- **`snapshot_*`**：无 date 参数的全市场横截面（发现"我们漏了哪些联赛"）

## 归档覆盖度（导出时）

- 覆盖日期：{', '.join(cov['dates']) or '（无）'}
- 归档文件数：{cov['n_archive_files']}
- 全市场横截面快照：{cov['snapshot_files']} 个

### 缺口（到期后无法补上，需在 10-15 前抢）

| 日期 | 端点 | 已有/应有页 | 缺记录数 |
|---|---|---|---|
{gap_lines}

## 怎么用

```bash
# 1) 把 archive/ 放回仓库对应位置（若需要重新加工）
cp -r archive/* <repo>/data/state/njstats_archive/

# 2) 从归档重建特征
python -m engine.sources.njstats_features

# 3) 特征输出在 data/state/njstats_features/<date>.json
```

## 数据纪律

- 原始归档**不进 git**（体积 33MB+，且每小时新增）；派生特征才进
- 到期后这批数据不可再得，请至少保留两份副本（如本机 + 外置盘）
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="NJStats 归档导出")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="导出根目录")
    ap.add_argument("--tar", action="store_true", help="额外打包 tar.gz")
    ap.add_argument("--verify", action="store_true", help="只校验不导出")
    args = ap.parse_args(argv)

    cov = coverage()
    print(f"归档文件 {cov['n_archive_files']} 个，日期 {len(cov['dates'])} 天，"
          f"横截面快照 {cov['snapshot_files']} 个")
    if cov["prob_gaps"]:
        print(f"⚠ 有 {len(cov['prob_gaps'])} 个 prob 分页缺口（到期后无法补）：")
        for g in cov["prob_gaps"][:8]:
            print(f"    {g['date']} {g['endpoint']}: {g['have_pages']}/{g['need_pages']} 页")
    else:
        print("✅ prob 分页无缺口")

    if args.verify:
        return 0

    stamp = datetime.now().strftime("%Y-%m-%d")
    dest = Path(args.out) / stamp
    if dest.exists():
        print(f"目标已存在，覆盖: {dest}")
    dest.mkdir(parents=True, exist_ok=True)

    n_arch = n_feat = 0
    total_bytes = 0
    if ARCHIVE.exists():
        dst = dest / "archive"
        dst.mkdir(exist_ok=True)
        for f in sorted(ARCHIVE.glob("*.json")):
            shutil.copy2(f, dst / f.name)
            n_arch += 1
            total_bytes += f.stat().st_size
    if FEATURES.exists():
        dst = dest / "features"
        dst.mkdir(exist_ok=True)
        for f in sorted(FEATURES.glob("*.json")):
            shutil.copy2(f, dst / f.name)
            n_feat += 1
            total_bytes += f.stat().st_size

    manifest = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(ARCHIVE),
        "n_archive_files": n_arch,
        "n_feature_files": n_feat,
        "total_bytes": total_bytes,
        "coverage": cov,
        "trial_expires": "2026-10-15T00:08:00+08:00",
        "note": "到期后 prob 端点历史页永久不可得；archive/ 按政策不入 git",
    }
    (dest / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (dest / "README.md").write_text(build_readme(cov, stamp), encoding="utf-8")

    print(f"已导出 {n_arch} 归档 + {n_feat} 特征 → {dest} ({total_bytes/1e6:.1f}MB)")

    if args.tar:
        tarpath = dest.with_suffix(".tar.gz")
        with tarfile.open(tarpath, "w:gz") as tf:
            tf.add(dest, arcname=dest.name)
        print(f"已打包 → {tarpath} ({tarpath.stat().st_size/1e6:.1f}MB)")
        print("  校验:", sha256(tarpath))

    return 0


if __name__ == "__main__":
    sys.exit(main())
