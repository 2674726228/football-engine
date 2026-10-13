"""归档安全网测试（2026-10-13，事故驱动）。

## 真实事故

复盘轮跑 `git reset --hard origin/main` 对齐远端，工作副本归档从
**196 个文件掉到 72 个**——归档刚被 `git rm --cached` 移出索引，
reset 把"已跟踪"的那部分回退了。救回来的唯一原因是几小时前做过导出。

## 所以锁三件事

1. **只增不删**：备份里的文件永不被覆盖或删除。宁可多几个过期文件，
   也不能因一次误操作把好的覆盖成坏的。
2. **不写进仓库**：仓库内的备份等于没有备份（reset/clean 一样清掉）。
3. **有差异必须报出来**：静默不同步 = 事故时才发现没有备份。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "njstats_safeguard.py"
sys.path.insert(0, str(ROOT / "scripts"))

import njstats_safeguard as ns  # noqa: E402


def test_sync_is_add_only(tmp_path):
    """只增不删：已存在的文件不被覆盖（误操作不能污染好备份）。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.json").write_text("NEW", encoding="utf-8")
    (dst / "a.json").write_text("OLD-PRECIOUS", encoding="utf-8")

    added, skipped = ns.sync(src, dst, report_only=False)
    assert added == 0 and skipped == 1
    assert (dst / "a.json").read_text(encoding="utf-8") == "OLD-PRECIOUS", \
        "备份里的文件被覆盖了——只增不删被破坏"


def test_sync_copies_new_files(tmp_path):
    """新文件必须被同步（这是安全网的本职）。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    for n in ("a.json", "b.json", "c.json"):
        (src / n).write_text("{}", encoding="utf-8")

    added, skipped = ns.sync(src, dst, report_only=False)
    assert added == 3 and skipped == 0
    assert len(list(dst.glob("*.json"))) == 3


def test_report_only_writes_nothing(tmp_path):
    """--check 必须真的不落盘（否则"检查"会污染备份状态）。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "a.json").write_text("{}", encoding="utf-8")

    added, _ = ns.sync(src, dst, report_only=True)
    assert added == 1, "报告模式应统计出待同步数"
    assert not dst.exists() or not list(dst.glob("*.json")), \
        "报告模式写了文件"


def test_backup_target_outside_repo(tmp_path, monkeypatch):
    """默认备份目录必须在仓库外。

    写在仓库里等于没有备份：git reset --hard / clean 一样会清掉，
    而事故恰恰就是 reset 引起的。
    """
    assert not str(ns.DEFAULT_BACKUP).startswith(str(ns.ROOT)), \
        f"默认备份在仓库内: {ns.DEFAULT_BACKUP}"


def test_repo_internal_target_is_rejected(tmp_path):
    """显式指定仓库内目录时必须拒绝（不能让人误配）。"""
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--to", str(ROOT / "tmp-backup"), "--check"],
        capture_output=True, text=True, timeout=60)
    # 无归档时提前返回 0；有归档时应因"仓库内"返回 1
    if ns.ARCHIVE.exists() and list(ns.ARCHIVE.glob("*.json")):
        assert r.returncode == 1, "仓库内备份目录未被拒绝"
        assert "仓库内" in r.stdout


def test_env_var_selects_backup(monkeypatch, tmp_path):
    """NJSTATS_BACKUP 环境变量必须生效（Air 上指向外置盘）。"""
    monkeypatch.setenv("NJSTATS_BACKUP", str(tmp_path / "ext"))
    assert ns.resolve_backup(None) == tmp_path / "ext"


def test_cli_takes_priority_over_env(monkeypatch, tmp_path):
    """--to 优先于环境变量。"""
    monkeypatch.setenv("NJSTATS_BACKUP", str(tmp_path / "env"))
    assert ns.resolve_backup(str(tmp_path / "cli")) == tmp_path / "cli"


def test_cli_runs_end_to_end(tmp_path):
    """端到端：能跑通并写出 LAST_SYNC 标记。"""
    if not ns.ARCHIVE.exists() or not list(ns.ARCHIVE.glob("*.json")):
        pytest.skip("无归档可备份")
    out = tmp_path / "backup"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--to", str(out)],
        capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr
    assert list((out / "archive").glob("*.json")), "没有备份文件"
    assert (out / "LAST_SYNC.txt").exists(), "缺少同步标记"
