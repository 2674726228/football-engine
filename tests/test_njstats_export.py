"""NJStats 归档导出/覆盖度体检的测试（2026-10-13）。

为什么值得测：
试用 2026-10-15 00:08 到期，到期后 prob 历史页**永久不可得**。
`coverage()` 是"到期前还缺什么"的唯一告警来源——它算错就会让人
以为已经采全了，实际漏了 65% 的数据（这正是原实现踩过的坑：
写死 2 页而实际有 6 页）。

所以锁三件事：
1. 缺口能被发现（缺页必须报出来，不能静默算成"齐了"）
2. 页数口径正确（p1 无后缀、p2+ 有 `_pN` 后缀）
3. 导出不往仓库里写（原始归档按 .gitignore 政策移出 git，导出再塞回去
   就等于白清理）
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "njstats_export.py"

pytest.importorskip("scripts.njstats_export")

sys.path.insert(0, str(ROOT / "scripts"))
import njstats_export as ne  # noqa: E402


def test_expected_pages_matches_reality():
    """期望页数必须与实际相符（实测 pageSize=100 时 maxPage=6, total=571）。

    写成 2 就是原实现那个坑：只采 200/571 条还显示"完成"。
    """
    assert ne.EXPECTED_PROB_PAGES == 6, "页数口径变了——重新核对 maxPage"
    assert len(ne.PROB_ENDPOINTS) == 8, "prob 端点数变了"


def test_coverage_finds_gaps(tmp_path, monkeypatch):
    """缺页必须被报出来（这是到期前抢数据的唯一依据）。"""
    monkeypatch.setattr(ne, "ARCHIVE", tmp_path)
    # 造一个只有 2 页的端点 + 一个满 6 页的端点
    for p in (1, 2):
        (tmp_path / f"2026-10-12_prob_draw{'' if p == 1 else f'_p{p}'}.json").write_text("{}", encoding="utf-8")
    for p in range(1, 7):
        (tmp_path / f"2026-10-12_prob_over{'' if p == 1 else f'_p{p}'}.json").write_text("{}", encoding="utf-8")

    cov = ne.coverage()
    gaps = {(g["date"], g["endpoint"]): g for g in cov["prob_gaps"]}
    assert ("2026-10-12", "draw") in gaps, "缺 4 页的 draw 没被发现"
    assert gaps[("2026-10-12", "draw")]["have_pages"] == 2
    assert gaps[("2026-10-12", "draw")]["need_pages"] == 6
    assert gaps[("2026-10-12", "draw")]["missing_records"] == 400
    assert ("2026-10-12", "over") not in gaps, "满页的 over 被误报为缺口"


def test_coverage_detects_endpoint_entirely_missing(tmp_path, monkeypatch):
    """整个端点一条没采（0 页）也必须报——0 页比缺 4 页更危险。"""
    monkeypatch.setattr(ne, "ARCHIVE", tmp_path)
    (tmp_path / "2026-10-12_prob_draw.json").write_text("{}", encoding="utf-8")
    cov = ne.coverage()
    gaps = {(g["date"], g["endpoint"]): g for g in cov["prob_gaps"]}
    assert gaps[("2026-10-12", "btts")]["have_pages"] == 0
    assert gaps[("2026-10-12", "btts")]["missing_records"] == 600


def test_coverage_counts_snapshots(tmp_path, monkeypatch):
    """全市场横截面快照要单独计数（它是发现漏联赛的唯一依据）。"""
    monkeypatch.setattr(ne, "ARCHIVE", tmp_path)
    for p in range(1, 4):
        name = "2026-10-12_snapshot_match.json" if p == 1 else f"2026-10-12_snapshot_match_p{p}.json"
        (tmp_path / name).write_text("{}", encoding="utf-8")
    cov = ne.coverage()
    assert cov["snapshot_files"] == 3


def test_page_naming_convention(tmp_path, monkeypatch):
    """p1 无后缀、p2+ 带 `_pN`——命名错了会把"已有"算成"缺"。

    这是实际踩过的 bug：归档脚本第一页写 `{date}_prob_draw.json`，
    第二页起写 `{date}_prob_draw_p2.json`。覆盖度检查必须用同一约定。
    """
    monkeypatch.setattr(ne, "ARCHIVE", tmp_path)
    # 用正确约定造满 6 页
    names = ["2026-10-12_prob_draw.json"] + [
        f"2026-10-12_prob_draw_p{p}.json" for p in range(2, 7)
    ]
    for n in names:
        (tmp_path / n).write_text("{}", encoding="utf-8")
    cov = ne.coverage()
    assert not [g for g in cov["prob_gaps"] if g["endpoint"] == "draw"], \
        "用正确命名造的满页被算成缺口——页数口径不一致"


def test_export_goes_outside_repo(tmp_path, monkeypatch):
    """导出目标不能落在仓库内。

    原始归档刚按 .gitignore 政策移出 git（33MB+，每小时新增）；
    若导出默认写回仓库目录，等于把清理成果又塞回去。
    """
    out = Path(ne.DEFAULT_OUT); rp = Path(ne.ROOT)
    assert not (out == rp or rp in out.parents), \
        f"默认导出目录在仓库内: {ne.DEFAULT_OUT}"
    assert ne.DEFAULT_OUT == Path.home() / "football-engine-archive"


def test_readme_lists_gaps():
    """导出的 README 必须列出缺口（人拿到数据包要知道还缺什么）。"""
    cov = {
        "dates": ["2026-10-12"],
        "prob_gaps": [{"date": "2026-10-12", "endpoint": "draw",
                       "have_pages": 2, "need_pages": 6, "missing_records": 400}],
        "snapshot_files": 0,
        "n_archive_files": 10,
    }
    text = ne.build_readme(cov, "2026-10-13")
    assert "2026-10-12" in text and "draw" in text
    assert "2/6" in text
    assert "400" in text
    assert "10-15" in text, "README 必须写明试用到期日"


def test_readme_handles_no_gaps():
    """无缺口时也要说清楚（而不是显示一个空表）。"""
    cov = {"dates": ["2026-10-12"], "prob_gaps": [], "snapshot_files": 3,
           "n_archive_files": 50}
    text = ne.build_readme(cov, "2026-10-13")
    assert "无缺口" in text


def test_cli_verify_runs():
    """--verify 必须能跑通（它是到期前的例行体检命令）。"""
    r = subprocess.run([sys.executable, str(SCRIPT), "--verify"],
                       capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, f"--verify 失败: {r.stderr}"
    assert "归档文件" in r.stdout


def test_manifest_shape(tmp_path, monkeypatch):
    """MANIFEST 必须含到期提醒与来源路径（数据包要能自证出处）。"""
    monkeypatch.setattr(ne, "ARCHIVE", tmp_path)
    monkeypatch.setattr(ne, "FEATURES", tmp_path / "feat")
    (tmp_path / "2026-10-12_prob_draw.json").write_text("{}", encoding="utf-8")
    out = tmp_path / "exported"
    # 子进程重新 import 模块，父进程的 monkeypatch 对它无效，必须走环境变量
    # （NJSTATS_ARCHIVE_DIR / NJSTATS_FEATURES_DIR）。否则子进程会用真实的
    # data/state/njstats_archive/——该目录按政策不入 git，fresh clone 与 CI
    # 上不存在，于是 n_archive_files=0，测试只在"本地采过归档"的机器上绿。
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out)],
        capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
        env={**__import__("os").environ,
             "PYTHONPATH": str(ROOT),
             "NJSTATS_ARCHIVE_DIR": str(tmp_path),
             "NJSTATS_FEATURES_DIR": str(tmp_path / "feat")},
    )
    assert r.returncode == 0, r.stderr
    dest_dirs = list(out.iterdir())
    assert dest_dirs, "没有导出目录"
    mf = json.loads((dest_dirs[0] / "MANIFEST.json").read_text(encoding="utf-8"))
    assert mf["trial_expires"].startswith("2026-10-15")
    assert "source" in mf
    assert mf["n_archive_files"] >= 1
    assert Path(mf["source"]) == tmp_path, "MANIFEST.source 必须指向被测目录"
