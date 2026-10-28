"""CI「状态回仓」必须真的提交（2026-10-22 审计）。

原步骤：

    git add data/state/challenge_state.json || true
    git diff --cached --quiet || (git pull --rebase origin main || git rebase --abort; git push origin HEAD:main)

它 `git add` 之后**从来没有 commit**：HEAD 还是 checkout 出来的那个提交，
`git push origin HEAD:main` 只会回 "Everything up-to-date"（退出码 0）。
于是 CI 投出去的注——预登记记录、成交赔率、used 配额——一条都不进仓，而这一步是绿的。
duty 看门狗补触发 CI 的时机恰好是本机合盖睡着的那几轮，也就是只有 CI 能落记录的时候。

用真 git 跑，不做文本断言：文本里确实有 `git push`，看着一切正常。
"""
import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "challenge.yml"
STATE = "data/state/challenge_state.json"


def _bash() -> str | None:
    """返回能处理 Windows 路径的 bash（Git Bash）；没有就返回 None。

    2026-10-28：Windows 的 CreateProcess 先命中 System32 里的 WSL 垫片，
    `bash -c` 进 WSL 后 Windows 路径被翻成 /mnt/c/…，本地裸库 remote 记下的
    URL 之后再也找不到（克隆出来的 origin 是 /mnt/c/... → 所有 git 操作失败）。
    """
    if os.name != "nt":
        return "bash"
    candidates = []
    git = shutil.which("git")
    if git:
        candidates.append(str(Path(git).resolve().parent.parent / "bin" / "bash.exe"))
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            candidates.append(str(Path(base) / "Git" / "bin" / "bash.exe"))
    return next((c for c in candidates if Path(c).exists()), None)


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="需要 Git Bash / POSIX bash")


def sh(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", cmd], cwd=str(cwd),
                          capture_output=True, text=True, timeout=180,
                          encoding="utf-8", errors="replace")


def commit_back_block() -> str:
    """抠出「状态回仓」的 run 脚本，剔除只在 GitHub 上才成立的 remote 改写行。"""
    # 2026-10-28：统一换行。Windows（core.autocrlf=true）检出为 CRLF 时，
    # 抠出来的脚本每行末尾带 \r，交给 bash 后命令名被污染 → 真 git 一步都不执行。
    text = WF.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(r"- name: 状态回仓\n(?:\s*#.*\n)*\s+run: \|\n((?:\s{10,}.*\n|\s*\n)+)", text)
    assert m, "找不到 状态回仓 步骤"
    lines = []
    for ln in m.group(1).split("\n"):
        if not ln.strip():
            lines.append("")
            continue
        body = ln[10:]
        if "remote set-url" in body:      # 测试环境用本地裸库
            continue
        lines.append(body)
    return "\n".join(lines)


@pytest.fixture
def env(tmp_path: Path):
    remote = tmp_path / "remote.git"
    sh(f'git init -q --bare "{remote}" && git -C "{remote}" symbolic-ref HEAD refs/heads/main',
       tmp_path)
    a = tmp_path / "a"
    sh(f'git clone -q "{remote}" a', tmp_path)
    sh("git config user.email t@t && git config user.name t", a)
    (a / "data" / "state").mkdir(parents=True)
    (a / STATE).write_text('{"week":"2026-W43","used":0,"records":[]}', encoding="utf-8")
    sh("git add -A && git commit -qm base && git push -q origin HEAD:main", a)
    return remote, a


def test_ci_state_lands_on_remote_when_nobody_else_moved(env):
    """远端没被别人推过：CI 改了 state，这条记录就必须出现在 main 上。"""
    remote, a = env
    (a / STATE).write_text('{"week":"2026-W43","used":3,'
                           '"records":[{"match":"CI注 vs 对手","pick":"主胜@1.90"}]}',
                           encoding="utf-8")
    r = sh(commit_back_block(), a)
    assert r.returncode == 0, r.stdout + r.stderr

    log = sh(f'git --git-dir="{remote}" log --oneline', a).stdout
    assert log.count("\n") >= 2, "回仓步从未产生提交：push 的是 checkout 出来的旧 HEAD"
    body = sh(f'git --git-dir="{remote}" show main:{STATE}', a).stdout
    assert "CI注" in body, "CI 提交的注没有进仓（步骤却是绿的）"


def test_conflicting_state_fails_loudly(env):
    """本机 duty 抢先推过同一个文件：不能 -X ours/theirs 悄悄丢一侧记录，必须红。"""
    remote, a = env
    tmp = a.parent
    b = tmp / "b"
    sh(f'git clone -q "{remote}" b', tmp)
    sh("git config user.email o@o && git config user.name o", b)
    (b / STATE).write_text('{"week":"2026-W43","used":2,'
                           '"records":[{"match":"本机注 vs 对手","pick":"客胜@2.10"}]}',
                           encoding="utf-8")
    sh("git commit -qam 'duty: challenge state 更新' && git push -q origin HEAD:main", b)

    (a / STATE).write_text('{"week":"2026-W43","used":3,'
                           '"records":[{"match":"CI注 vs 对手","pick":"主胜@1.90"}]}',
                           encoding="utf-8")
    r = sh(commit_back_block(), a)
    out = r.stdout + r.stderr
    assert r.returncode != 0, \
        "两边都改了 state 却返回 0 —— 记录被静默丢弃，E8/E10 样本会悄悄变少"
    assert "Pulling is not possible because you have unmerged files" not in out, \
        "失败的 rebase 没被 abort，下一轮 pull 会永久卡死"
    upstream = sh(f'git --git-dir="{remote}" show main:{STATE}', a).stdout
    assert upstream.count("本机注") == 1 and "CI注" not in upstream, \
        "冲突处理悄悄改了远端记录（必须保留先推的一方，失败留给下一轮自愈）"
    assert "rebase" not in sh(f'git -C "{a}" status --short', a).stdout.lower(), \
        "退出时仓库还停在 rebase 中间状态"


def _load_duty():
    spec = importlib.util.spec_from_file_location(
        "njstats_duty_commitback", ROOT / "scripts" / "njstats_duty.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_duty_commit_push_aborts_conflicting_rebase(env, monkeypatch):
    """上游抢先改同一个文件时，duty 必须 abort，并把本地提交留在原地等重放。

    原实现 pull 失败后不 abort：仓库停在「rebase 进行中 + 未合并文件」，
    下一轮开头那句 pull --rebase 直接报 unmerged files——这台机器就再也
    提交不了任何东西。CI 那侧 2026-10-22 开始真提交后，这个窗口才会出现。
    """
    remote, a = env
    duty = _load_duty()
    monkeypatch.setattr(duty, "ROOT", a)
    monkeypatch.setattr(duty.time, "sleep", lambda s: None)
    logs: list[str] = []
    monkeypatch.setattr(duty, "log", logs.append)

    tmp = a.parent
    b = tmp / "b"
    sh(f'git clone -q "{remote}" b', tmp)
    sh("git config user.email o@o && git config user.name o", b)
    (b / STATE).write_text('{"used":9,"records":["本机注"]}', encoding="utf-8")
    sh("git commit -qam 'upstream 抢先' && git push -q origin HEAD:main", b)

    (a / STATE).write_text('{"used":3,"records":["CI注"]}', encoding="utf-8")
    assert duty.commit_push([STATE], "duty: test state") is False

    joined = " | ".join(logs)
    assert "abort" in joined, f"冲突没有被 abort，日志: {joined}"
    assert not (a / ".git" / "rebase-merge").exists() and not (a / ".git" / "rebase-apply").exists(), \
        "仓库停在 rebase 中间态，下一轮 duty 直接瘫掉"
    assert "duty: test state" in sh(f'git -C "{a}" log --oneline', a).stdout, "本地提交被丢掉"


def test_duty_distinguishes_network_failure_from_conflict(env, monkeypatch):
    """远端不可达时 abort 不会成功——日志必须写「pull 失败」而不是「冲突」。

    2026-10-22 20:35 实例：pull 报 "and the repository exists."（远端瞬时不可达），
    旧日志却统一写成「冲突，已 abort」，会把网络问题和内容冲突混为一谈。
    """
    remote, a = env
    duty = _load_duty()
    monkeypatch.setattr(duty, "ROOT", a)
    monkeypatch.setattr(duty.time, "sleep", lambda s: None)
    logs: list[str] = []
    monkeypatch.setattr(duty, "log", logs.append)

    sh("git remote set-url origin /nonexistent/repo.git", a)
    (a / STATE).write_text('{"used":1,"records":[]}', encoding="utf-8")
    assert duty.commit_push([STATE], "duty: test net") is False

    joined = " | ".join(logs)
    assert "pull 失败（未进入 rebase）" in joined, joined
    assert "pull 冲突" not in joined, joined
    assert not (a / ".git" / "rebase-merge").exists()
