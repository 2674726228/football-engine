#!/usr/bin/env python3
"""确定性挑战值守（零模型版）——launchd 每小时 :05/:35 触发。

流程：cookie→mint JWT→（变化时）推 GitHub secret→跑 challenge_hourly→state 冲突自愈→commit/push。
退出码：0 正常；20 需人工登录（浏览器 session 死，跑 scripts/njstats_seed_login.py 重新播种）。
依赖：~/.football-engine/njstats_session.json（由 seed_login 播种）、pynacl、requests。
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
SESS = HOME / ".football-engine" / "njstats_session.json"
JWT_FILE = HOME / ".football-engine" / "njstats_jwt"
LOG = HOME / ".football-engine" / "duty.log"
BASE = "https://www.njstats.cn"
REPO = "wlrwx/football-engine"
PX = {"http": None, "https": None}  # 直连；显式 None 防环境代理回落
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def log(msg: str) -> None:
    line = f"{time.strftime('%m-%d %H:%M:%S')} {msg}"
    print(line)
    try:
        if LOG.exists() and LOG.stat().st_size > 512_000:
            LOG.rename(LOG.with_suffix(".log.old"))
        with LOG.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def sh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)


def autoheal_session() -> bool:
    """SESSION cookie 过期时用持久 profile 的 remember-me 无头续期，无需人工过验证码。

    只在 mint 拿到非 200 时调用。失败返回 False，由调用方按「需人工播种」处理。
    """
    if not (HOME / ".football-engine" / "pwprofile").exists():
        return False
    p = subprocess.run([str(ROOT / ".venv" / "bin" / "python3"),
                        "scripts/njstats_refresh_session.py"],
                       cwd=ROOT, capture_output=True, text=True, check=False, timeout=180)
    ok = p.returncode == 0
    log(f"续期脚本 rc={p.returncode} {(p.stdout or p.stderr).strip().splitlines()[-1] if (p.stdout or p.stderr).strip() else ''}")
    return ok


def mint() -> str | None:
    if not SESS.exists():
        log("无 session 文件，需播种登录")
        return None
    cookie = json.loads(SESS.read_text()).get("SESSION", "")
    if not cookie:
        log("session 文件缺 SESSION")
        return None
    try:
        r = requests.get(f"{BASE}/api/user/current",
                         headers={"Cookie": f"SESSION={cookie}", "User-Agent": UA,
                                  "Accept": "application/json", "Referer": BASE + "/prediction/mine"},
                         timeout=20, proxies=PX)
    except requests.RequestException as e:
        log(f"mint 网络异常 {e}")
        return None
    if r.status_code != 200:
        log(f"mint HTTP {r.status_code}（session 死，尝试无头续期）")
        if not autoheal_session():
            log("无头续期失败，需人工登录（跑 njstats_seed_login.py）")
            return None
        cookie = json.loads(SESS.read_text()).get("SESSION", "")
        r = requests.get(f"{BASE}/api/user/current",
                         headers={"Cookie": f"SESSION={cookie}", "User-Agent": UA,
                                  "Accept": "application/json", "Referer": BASE + "/prediction/mine"},
                         timeout=20, proxies=PX)
        if r.status_code != 200:
            log(f"续期后仍 HTTP {r.status_code}，需人工登录")
            return None
        log("无头续期成功")
    tok = ((r.json() or {}).get("data") or {}).get("token")
    return tok or None


def push_secret(jwt: str) -> None:
    try:
        from nacl.encoding import Base64Encoder
        from nacl.public import PublicKey, SealedBox
    except ImportError:
        log("无 pynacl，跳过推 secret"); return
    p = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True, cwd=ROOT, check=False)
    tok = next((l.split("=", 1)[1] for l in p.stdout.splitlines() if l.startswith("password=")), "")
    # launchd 环境可能碰不到 keychain：文件 token 优先
    ftok = HOME / ".football-engine" / "github_token"
    if ftok.exists():
        tok = ftok.read_text().strip() or tok
    if not tok:
        log("无 GitHub token，跳过推 secret"); return
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    pk = requests.get(f"https://api.github.com/repos/{REPO}/actions/secrets/public-key", headers=h, timeout=30, proxies=PX).json()
    if "key" not in pk:
        log(f"取 public-key 失败 {pk.get('message')}"); return
    sealed = SealedBox(PublicKey(pk["key"], Base64Encoder)).encrypt(jwt.encode())
    rr = requests.put(f"https://api.github.com/repos/{REPO}/actions/secrets/NJSTATS_JWT", headers=h,
                      json={"key_id": pk["key_id"], "encrypted_value": base64.b64encode(sealed).decode()},
                      timeout=30, proxies=PX)
    log(f"推 secret → {rr.status_code}")


def fix_conflicts() -> None:
    """state 并发写冲突自愈：取本地新生成侧（stash 侧），逐个 JSON 校验后提交。"""
    u = sh("git", "diff", "--name-only", "--diff-filter=U").stdout.split()
    if not u:
        return
    files = " ".join(u)
    sh("sh", "-c", f"git checkout stash@{{0}} -- {files} 2>/dev/null || git checkout --theirs -- {files}")
    sh("git", "stash", "drop")
    bad = []
    for f in u:
        if f.endswith(".json"):
            try:
                json.loads((ROOT / f).read_text())
            except Exception:
                bad.append(f)
    if bad:
        # 坏 JSON 回退到远端版本
        sh("git", "checkout", "origin/main", "--", *bad)
        log(f"坏 JSON 回退远端: {bad}")
    sh("git", "add", *u)
    sh("git", "commit", "-m", "duty: state 并发写冲突自愈（取新生成侧）")
    log(f"冲突已清: {len(u)} 文件")


def main() -> int:
    os.chdir(ROOT)
    sh("git", "pull", "--rebase", "--autostash", "origin", "main")
    if sh("git", "diff", "--name-only", "--diff-filter=U").stdout.strip():
        fix_conflicts()
    tok = mint()
    if not tok:
        log("本轮：mint 失败，需人工登录（跑 njstats_seed_login.py）")
        return 20
    old = JWT_FILE.read_text().strip() if JWT_FILE.exists() else ""
    JWT_FILE.write_text(tok)
    os.chmod(JWT_FILE, 0o600)
    if tok != old:
        push_secret(tok)
        log("mint(新)")
    else:
        log("mint(无变化)")
    env = dict(os.environ, NJSTATS_JWT=tok)
    p = subprocess.run([str(ROOT / ".venv" / "bin" / "python3"), "scripts/challenge_hourly.py"],
                       cwd=ROOT, capture_output=True, text=True, env=env, check=False)
    tail = (p.stdout or p.stderr).strip().splitlines()
    log("1x2: " + (tail[-1] if tail else "无输出"))
    st = sh("git", "status", "--short", "data/state/challenge_state.json")
    if st.stdout.strip():
        sh("git", "add", "data/state/challenge_state.json")
        sh("git", "commit", "-m", "duty: challenge state 更新")
        for _ in range(3):
            if sh("git", "pull", "--rebase", "--autostash", "origin", "main").returncode == 0 and sh("git", "push", "origin", "main").returncode == 0:
                log("state 已推"); break
            time.sleep(4)
    # 社区倾向快照（E6 观测，见 docs/EXPERIMENTS.md）。放在最后：前面 1x2 该投
    # 的都投完了，这一步再慢/再失败都不会影响提交链。每天只跑一次（date 门控），
    # 用 marker 而不是看快照文件在不在——避免手工删了文件后一轮轮重跑。
    if os.environ.get("DUTY_SKIP_COMMUNITY") != "1":
        _maybe_community_snapshot()
    return 0


def _maybe_community_snapshot() -> None:
    """每天一轮把社区倾向落盘（站点无历史接口，只能自己攒）。"""
    marker = ROOT / "data" / "state" / ".community_snapshot_done"
    today = time.strftime("%Y-%m-%d")
    try:
        if marker.exists() and marker.read_text().strip() == today:
            return
    except OSError:
        pass
    p = subprocess.run([str(ROOT / ".venv" / "bin" / "python3"),
                        "scripts/njstats_community_snapshot.py"],
                       cwd=ROOT, capture_output=True, text=True, check=False, timeout=300)
    out = (p.stdout or p.stderr).strip().splitlines()
    log("community: " + (out[-1] if out else f"无输出 rc={p.returncode}"))
    if p.returncode == 0:
        try:
            marker.write_text(today)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
