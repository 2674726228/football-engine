#!/usr/bin/env python3
"""无头续期 NJStats SESSION cookie——session 过期时自愈，不需要人工过验证码。

原理：seed_login 用的持久 profile（~/.football-engine/pwprofile）里除了会话级
SESSION，还存着长效的 remember-me cookie。只要 remember-me 没过期，无头启动
chromium 后站点会用它静默换发一枚新 SESSION，直接抓下来写回
~/.football-engine/njstats_session.json 即可，全程不出现验证码、不弹窗。

只有当 remember-me 也过期（约 14 天，或用户主动退出登录/改密码）时才会失败，
那时才需要人工跑 scripts/njstats_seed_login.py 弹浏览器登录一次。

用法：
    .venv/bin/python3 scripts/njstats_refresh_session.py          # 续期并写文件
    .venv/bin/python3 scripts/njstats_refresh_session.py --check  # 只检测不写

退出码：0 成功；1 失败（需人工播种）；2 --check 下未安装 playwright。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HOME = Path.home()
PROFILE = HOME / ".football-engine" / "pwprofile"
OUT = HOME / ".football-engine" / "njstats_session.json"
BASE = "https://www.njstats.cn"


def refresh(check_only: bool = False) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("未安装 playwright，无法无头续期")
        return 2

    if not PROFILE.exists():
        print(f"无持久 profile {PROFILE}，需人工播种（跑 scripts/njstats_seed_login.py）")
        return 1

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE), headless=True, viewport={"width": 1100, "height": 800}
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
            # 用接口确认登录态是否真的还在（页面 200 不代表已登录）
            st = page.evaluate("""async () => {
              const r = await fetch('/api/user/current', {credentials:'include', cache:'no-store'});
              return r.status;
            }""")
            if st != 200:
                print(f"登录态已失效（HTTP {st}），需人工播种（跑 scripts/njstats_seed_login.py）")
                return 1
            cookies = {
                c["name"]: c["value"]
                for c in ctx.cookies()
                if c.get("domain", "").endswith("njstats.cn")
            }
        finally:
            ctx.close()

    if "SESSION" not in cookies:
        print("登录态有效但未取到 SESSION cookie")
        return 1

    if check_only:
        print(f"可无头续期（SESSION len={len(cookies['SESSION'])}, 另有 {sorted(k for k in cookies if k != 'SESSION')}）")
        return 0

    OUT.write_text(json.dumps({"SESSION": cookies["SESSION"], "seeded_at": time.strftime("%F %T")}))
    OUT.chmod(0o600)
    print(f"✅ SESSION 已续期 → {OUT}（seeded_at {time.strftime('%F %T')}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="无头续期 NJStats SESSION cookie")
    ap.add_argument("--check", action="store_true", help="只检测是否可续期，不写文件")
    return refresh(ap.parse_args().check)


if __name__ == "__main__":
    sys.exit(main())
