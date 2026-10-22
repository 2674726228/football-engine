"""挑战子系统防火墙（CI 强制，docs/CHALLENGE_SUBSYSTEM.md 反馈合同的执行版）

边界不是"引擎不许碰 NJStats 的任何东西"，而是**不许碰裁判产物**：

1. 裁判产物（challenge_state / challenge_review / challenge_policy、站点结算
   结果、`dom_tendency` 方向真值）——进了模型就是自己批卷。
2. 赛前公开的侧挂数据（开盘赔率、主客分离统计、社区热度）——允许进，但只能
   经 `engine/sources/njstats_side.py` 这一个入口，且受时点/市场隔离约束。
3. scripts/njstats_* 与 challenge_review 不得 import engine.*（反向零依赖）。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = re.compile(r"challenge_state|challenge_review|challenge_policy")
# 真值标签：只能出现在文档里，不能让代码读它
FORBIDDEN_TRUTH_LABEL = re.compile(r"""[\[.]\s*["']dom_tendency["']""")
# 侧挂数据的落地路径
SIDE_PATHS = re.compile(r"njstats_archive|njstats_features|njstats/(?:community|site_odds)")
# 模型层里唯一允许读侧挂路径的两个文件（前者产出特征，后者是运行时入口）
SIDE_READERS = {"engine/sources/njstats_side.py", "engine/sources/njstats_features.py"}


def _engine_sources():
    for f in (ROOT / "engine").rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        yield f, f.read_text(encoding="utf-8", errors="ignore")


def test_engine_never_reads_challenge_outputs():
    for f, src in _engine_sources():
        assert not FORBIDDEN.search(src), (
            f"模型层读取挑战产物，违反反馈合同: {f}（见 docs/CHALLENGE_SUBSYSTEM.md）")


def test_engine_never_reads_truth_labels():
    for f, src in _engine_sources():
        assert not FORBIDDEN_TRUTH_LABEL.search(src), (
            f"模型层读取 dom_tendency 真值标签（泄漏赛果方向）: {f}")


def test_side_data_has_single_entry_point():
    """侧挂数据允许进，但入口收敛，换源必须改那个文件——这是"侧挂≠热插拔"的可执行版。"""
    bad = []
    for f, src in _engine_sources():
        rel = f.relative_to(ROOT).as_posix()
        if rel in SIDE_READERS:
            continue
        if SIDE_PATHS.search(src):
            bad.append(rel)
    assert not bad, f"侧挂数据绕过了 engine/sources/njstats_side.py: {bad}"


def test_challenge_scripts_dont_import_engine():
    for name in ("njstats_api_submit.py", "njstats_capture.py", "challenge_review.py"):
        f = ROOT / "scripts" / name
        if not f.exists():
            continue
        src = f.read_text(encoding="utf-8", errors="ignore")
        bad = [l for l in re.findall(r"^\s*(?:from|import)\s+(engine[.\w]*)", src, re.M)
               if l != "engine.beijing_time"]
        assert not bad, f"{name} 只许 import 时区工具，发现: {bad}（拔除零依赖原则）"
