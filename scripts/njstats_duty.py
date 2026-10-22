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
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import njstats_api_submit as api  # noqa: E402

HOME = Path.home()
SESS = HOME / ".football-engine" / "njstats_session.json"
JWT_FILE = HOME / ".football-engine" / "njstats_jwt"
LOG = HOME / ".football-engine" / "duty.log"
BASE = "https://www.njstats.cn"
REPO = "wlrwx/football-engine"
# GitHub Actions 里 Challenge (1x2) workflow 的数字 ID（gh api 可查）。
# 平台对公开仓库的定时任务限流严重（实测 24h 只跑 3/24 次），这个看门狗
# 在本机这一轮零提交、且 CI 已超 CI_STALE_H 小时没跑时补触发一轮。
CI_WORKFLOW_ID = 385270775
CI_STALE_H = 3.0
# duty 自己拥有的落盘路径。分成两组是因为提交时机不同：state 在投完注就要推
# （CI 那边靠它去重），三份快照在所有采集步跑完后才齐。
# 只列这三份，不整目录 add——njstats/ 下还有别的脚本的产物，
# 让 duty 顺手提交它们等于替别人决定版本，会和 ci-sync 抢写。
STATE_PATHS = ["data/state/challenge_state.json"]
SNAPSHOT_PATHS = ["data/state/njstats/site_odds/",
                  "data/state/njstats/my_predictions/",
                  "data/state/njstats/community/"]
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


def commit_push(paths: list[str], msg: str) -> bool:
    """把指定路径的工作区变更提交并推送（3 次重试）。无变更视为已完成，返回 True。

    2026-10-22 修：原先这里是一段只认 `challenge_state.json` 的内联 commit，
    而 duty 每天另外写的三份快照（site_odds / my_predictions / community）
    **从来没人提交**——它们只存在于这台笔记本的工作区里。站点赔率快照存在的
    唯一理由就是攒样本做配对回放（docs/CHALLENGE_ODDS_GAP_20261021.md），
    不进 origin 等于没攒；已知坑②「git reset --hard 清归档」已经发生过两次。
    与 rho 写回「进工作区无人提交」是同一个病。
    """
    if not sh("git", "status", "--short", "--", *paths).stdout.strip():
        return True
    sh("git", "add", "-A", "--", *paths)
    if sh("git", "commit", "-m", msg).returncode != 0:
        log(f"commit 失败: {msg}")
        return False
    for _ in range(3):
        if (sh("git", "pull", "--rebase", "--autostash", "origin", "main").returncode == 0
                and sh("git", "push", "origin", "main").returncode == 0):
            log(f"已推: {msg}")
            return True
        time.sleep(4)
    log(f"push 失败（3 次重试）: {msg}")
    return False


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
    # 本轮到底投了几注——看门狗要不要补跑 CI 取决于这个
    _submitted = 0
    for line in tail:
        m = re.search(r"提交\s+(\d+)\s+注", line)
        if m:
            _submitted = int(m.group(1))
            break
    commit_push(STATE_PATHS, "duty: challenge state 更新")
    # 社区倾向快照（E6 观测，见 docs/EXPERIMENTS.md）。放在最后：前面 1x2 该投
    # 的都投完了，这一步再慢/再失败都不会影响提交链。每天只跑一次（date 门控），
    # 用 marker 而不是看快照文件在不在——避免手工删了文件后一轮轮重跑。
    if os.environ.get("DUTY_SKIP_SERVER_SNAPSHOT") != "1":
        _maybe_server_records_snapshot()
    if os.environ.get("DUTY_SKIP_COMMUNITY") != "1":
        _maybe_community_snapshot()
    # 快照写完就走提交链。这一步失败不影响本轮采集（文件已落在本地），下一轮
    # 还会再试——但要让它可见，否则又回到「静默不提交、样本只活在笔记本上」。
    commit_push(SNAPSHOT_PATHS, "duty: njstats 每日快照（站点赔率/服务端真值/社区倾向）")
    # CI 看门狗：平台定时任务限流严重，CI 长时间没跑时补一轮。
    # 放在最后、且只在本地零提交时动手（见函数 docstring 的三条约束）。
    if os.environ.get("DUTY_SKIP_CI_WATCHDOG") != "1":
        _maybe_dispatch_ci(_submitted)
    return 0


def _ci_last_run_age_h() -> float | None:
    """GitHub Actions 里 Challenge workflow 距上次触发的小时数。

    只读 API，不改任何状态。取不到（无 token/网络/限流）时返回 None，
    调用方按「不知道」处理，不触发补跑。
    """
    tok = ""
    ftok = HOME / ".football-engine" / "github_token"
    if ftok.exists():
        try:
            tok = ftok.read_text().strip()
        except OSError:
            tok = ""
    if not tok:
        return None
    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{CI_WORKFLOW_ID}/runs"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {tok}",
                                       "Accept": "application/vnd.github+json"},
                         params={"per_page": 1}, timeout=25, proxies=PX)
        if r.status_code != 200:
            return None
        runs = (r.json() or {}).get("workflow_runs") or []
        if not runs:
            return None
        # created_at 是 UTC ISO8601（形如 2026-10-20T18:26:16Z）
        from datetime import datetime, timezone
        ts = datetime.fromisoformat(runs[0]["created_at"].replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
    except (requests.RequestException, KeyError, ValueError):
        return None


def _dispatch_ci_challenge() -> bool:
    """手动触发一轮 CI challenge（补跑）。返回是否成功受理。"""
    tok = ""
    ftok = HOME / ".football-engine" / "github_token"
    if ftok.exists():
        try:
            tok = ftok.read_text().strip()
        except OSError:
            tok = ""
    if not tok:
        return False
    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{CI_WORKFLOW_ID}/dispatches"
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {tok}",
                                        "Accept": "application/vnd.github+json"},
                          json={"ref": "main"}, timeout=25, proxies=PX)
        return r.status_code in (204, 201)
    except requests.RequestException:
        return False


def _maybe_dispatch_ci(local_submitted: int) -> None:
    """CI 长时间没跑时补触发一轮。

    2026-10-21 更新：去重已改走服务端真值（challenge_hourly 用
    `api.my_predictions` 取 msgId=11 的「我已预测」列表并入去重名单），
    所以**不再需要「本地零提交」这道闸**——CI 与本机即使完全并发，
    后到的那个也会看到服务端多出来的记录而跳过，不会重复下单。

    保留的两条约束是防刷爆和防叠加，与重复投注无关：
      1) 只在 CI 距上次运行超过 CI_STALE_H 小时时才补，正常调度时不叠加；
      2) 每小时最多补一次（marker），避免 3 小时空档里 6 轮 duty 各触发
         一次、堆出一串并发 run（那会挤占 Actions 配额，反过来加剧限流）。

    `local_submitted` 参数保留只为兼容旧调用点与日志，不再参与判断。
    """
    age = _ci_last_run_age_h()
    if age is None:
        return
    if age < CI_STALE_H:
        return
    marker = HOME / ".football-engine" / ".ci_watchdog_last"
    now = time.time()
    try:
        if marker.exists() and now - float(marker.read_text().strip()) < 3600:
            return
    except (OSError, ValueError):
        pass
    if _dispatch_ci_challenge():
        log(f"CI 看门狗: Challenge 已 {age:.1f}h 未跑，已补触发一轮")
        try:
            marker.write_text(str(int(now)))
        except OSError:
            pass


def _maybe_server_records_snapshot() -> None:
    """每次把服务端「我已预测」真值落盘，作为账本缺口的对照物。

    为什么需要：challenge_state.json 历史上出现过回写失败（5 条缺口），
    而那 5 条的服务端记录现在只能靠猜口径。以后每天存一份真值，
    一旦 state 再漏，就能直接 diff 出来、按当时的 market 码+赔率还原。
    成本很低（一次请求），但能让「账目对不上」从无解变成可追。

    落盘到 data/state/njstats/my_predictions/<YYYY-MM-DD>.json，
    每次覆盖当天（一天内多次轮询以最后一次为准，只会变多不会变少）。
    """
    try:
        recs = api.my_predictions()
    except Exception as e:  # noqa: BLE001 - 快照失败绝不能影响值守
        log(f"server-snapshot: 取数失败 {type(e).__name__}")
        return
    if recs is None:
        return
    out_dir = ROOT / "data" / "state" / "njstats" / "my_predictions"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y-%m-%d")
        payload = {
            "snapshot_at": time.strftime("%F %T"),
            "count": len(recs),
            "note": "msgId=11 账号私有「我已预测」服务端真值；与 challenge_state 对照用",
            "records": recs,
        }
        (out_dir / f"{day}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log(f"server-snapshot: 写盘失败 {e}")
        return

    # 站点赔率快照（含让球/大小球场次的 1x2 三档）。
    #
    # 为什么单独存：本地算 edge 用体彩官方赔率，而挑战赛按站点赔率结算，
    # 两者差约 10%（见 docs/CHALLENGE_ODDS_GAP_20261021.md）。站点赔率
    # 只在「当前板」可见，赛后就变了，不存下来永远没法回测「换成站点
    # 赔率算 EV 能不能提高命中」。每天存一份，攒够再做配对回放。
    try:
        board = api.board_with_odds()
    except Exception as e:  # noqa: BLE001
        log(f"odds-snapshot: 取数失败 {type(e).__name__}")
        return
    if not board:
        return
    with_odds = [b for b in board if b.get("odds")]
    odds_dir = ROOT / "data" / "state" / "njstats" / "site_odds"
    try:
        odds_dir.mkdir(parents=True, exist_ok=True)
        (odds_dir / f"{day}.json").write_text(json.dumps({
            "snapshot_at": time.strftime("%F %T"),
            "total": len(board),
            "with_odds": len(with_odds),
            "note": "msgId=1 全板 + 站点赔率（挑战赛结算口径）。与 predictions 的体彩赔率对照用",
            "matches": [{"sid": b["sid"], "home": b["home"], "away": b["away"],
                         "kick_ts": b["kick_ts"], "odds": b["odds"]} for b in with_odds],
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"odds-snapshot: 站点赔率 {len(with_odds)}/{len(board)} 场")
    except OSError as e:
        log(f"odds-snapshot: 写盘失败 {e}")


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


# ------------------------------------------------------------------ 并发锁
#
# 原先这把锁只写在 harness/run_duty.sh 里（2026-10-21 加，mkdir 而不是 flock：
# macOS 自带的 flock 只在部分版本有）。那正是 PROJECT.md「下一步②」记的缺陷
# ——脚本本身无锁，手动跑一轮撞上 :05/:35 就是两个进程并发
# `git pull --rebase --autostash` + 写 njstats_jwt + 写 challenge_state.json，
# 实测撞过一次（08:35:03 与 08:35:10 并存约 7 秒，侥幸没事）。
#
# 现在入口要从壳脚本换成 Qoder 自动化，壳要退役，所以锁必须搬进脚本本身：
# 否则删掉壳脚本 = 连同唯一那份并发保护一起删掉。
# 锁路径必须是**跨入口一致**的固定值。原先壳脚本用 ${TMPDIR:-/tmp}，
# 但 launchd 的 TMPDIR 是每用户私有目录（/var/folders/...），
# 而 launchd / Qoder 自动化 / 手工 shell 三者不一定拿到同一个值——
# 路径不一致 = 三个入口各锁各的，并发保护形同不存在。
# 凭据目录 ~/.football-engine 是所有入口都有的（本文件已经依赖它）。
LOCK = HOME / ".football-engine" / "duty.lock"
LOCK_STALE_SEC = 1800


def acquire_lock() -> bool:
    """拿到锁返回 True；已被占且锁龄未过陈限返回 False（调用方应直接退 0）。"""
    if LOCK.is_dir():
        try:
            age = time.time() - LOCK.stat().st_mtime
        except OSError:
            age = 0.0
        if age < LOCK_STALE_SEC:
            log(f"已有 duty 在跑（锁龄 {age:.0f}s），本轮跳过")
            return False
        log(f"清理陈锁（{age:.0f}s，进程被 kill -9 时不会自己清）")
        try:
            LOCK.rmdir()
        except OSError:
            return False
    try:
        LOCK.mkdir()
    except FileExistsError:
        log("抢锁失败（并发），本轮跳过")
        return False
    return True


def release_lock() -> None:
    try:
        LOCK.rmdir()
    except OSError:
        pass


def _locked_main() -> int:
    """进程级入口：壳脚本退役后，唯一能保证「同时只有一个 duty」的地方。

    `DUTY_SKIP_LOCK=1` 供手工调试用（明确知道自己不会撞车时）。
    测试直接调 main()，不走这里。
    """
    if os.environ.get("DUTY_SKIP_LOCK") == "1":
        return main()
    if not acquire_lock():
        return 0
    try:
        return main()
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(_locked_main())
