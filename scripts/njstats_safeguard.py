#!/usr/bin/env python3
"""NJStats 归档安全网：把当前工作副本同步到 git 之外的备份（2026-10-13）

## 为什么需要（真实事故驱动）

2026-10-13 复盘轮：为了把 commits 对齐远端，跑了一次 `git reset --hard origin/main`。
结果工作副本的归档从 **196 个文件掉到 72 个**——因为归档文件那时刚被
`git rm --cached` 移出索引，但**新的 .gitignore 规则还没被所有 checkout 认到**，
reset 把"已跟踪"的那部分回退掉了。

救回来的唯一原因：几小时前刚做过一次导出（`~/football-engine-archive/`）。
`cp -n` 补回 124 个文件。

**如果那次导出晚做一步，或没做，这批数据就永久没了**——
prob 端点的历史页在试用到期（10-15 00:08）后不可再得。

## 结论

导出不能是"手动想起来才做"的动作。归档在跑的时候，安全网也要跟着跑。

## 用法

    python scripts/njstats_safeguard.py            # 增量同步到备份
    python scripts/njstats_safeguard.py --check    # 只报告差异

设计：
- **只增不删**（`cp -n` 语义）：备份里的文件永远不被覆盖或删除。
  宁可备份多几个过期文件，也不能因为一次误操作把好的覆盖成坏的。
- 自动选目标：`--to` > 环境变量 `NJSTATS_BACKUP` > `~/football-engine-archive/live`
- 退出码：0 = 无需同步或同步成功；1 = 有差异但同步失败（CI 可据此报警）
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ARCHIVE = ROOT / "data" / "state" / "njstats_archive"
FEATURES = ROOT / "data" / "state" / "njstats_features"
DEFAULT_BACKUP = Path.home() / "football-engine-archive" / "live"


def resolve_backup(cli: str | None) -> Path:
    if cli:
        return Path(cli)
    env = os.environ.get("NJSTATS_BACKUP", "").strip()
    if env:
        return Path(env)
    return DEFAULT_BACKUP


def sync(src: Path, dst: Path, report_only: bool) -> tuple[int, int]:
    """增量同步（只增不删）。返回 (新增数, 跳过数)。"""
    if not src.exists():
        return 0, 0
    dst.mkdir(parents=True, exist_ok=True)
    added = skipped = 0
    for f in src.glob("*.json"):
        target = dst / f.name
        if target.exists():
            skipped += 1
            continue
        if report_only:
            added += 1
            continue
        shutil.copy2(f, target)
        added += 1
    return added, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="NJStats 归档安全网（增量备份到 git 之外）")
    ap.add_argument("--to", default="", help="备份目录")
    ap.add_argument("--check", action="store_true", help="只报告差异，不写")
    args = ap.parse_args(argv)

    backup = resolve_backup(args.to)
    n_src = len(list(ARCHIVE.glob("*.json"))) if ARCHIVE.exists() else 0
    if n_src == 0:
        print("njstats-safeguard: 工作副本无归档，跳过")
        return 0

    if str(backup).startswith(str(ROOT)):
        print(f"⚠ 备份目录在仓库内（{backup}）——请指定仓库外的位置")
        return 1

    a_arch, s_arch = sync(ARCHIVE, backup / "archive", args.check)
    a_feat, s_feat = sync(FEATURES, backup / "features", args.check)

    action = "待同步" if args.check else "已同步"
    print(f"njstats-safeguard: 工作副本 {n_src} 文件 | "
          f"archive 新增 {a_arch}（已存在 {s_arch}）| features 新增 {a_feat} | "
          f"{action} → {backup}")

    if a_arch or a_feat:
        stamp = backup / "LAST_SYNC.txt"
        if not args.check:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(
                f"{datetime.now().isoformat(timespec='seconds')}\n"
                f"archive_new={a_arch} features_new={a_feat} total_src={n_src}\n",
                encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
