#!/usr/bin/env python3
"""一次性播种登录：弹出真浏览器窗口 → 用户手工登录 njstats.cn → 导出 HttpOnly SESSION
cookie 到 ~/.football-engine/njstats_session.json，供 njstats_duty.py 无头 mint 用。

用法：./.venv/bin/python3 scripts/njstats_seed_login.py   （5 分钟窗口内完成登录）
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HOME = Path.home()
PROFILE = HOME / ".football-engine" / "pwprofile"
OUT = HOME / ".football-engine" / "njstats_session.json"
BASE = "https://www.njstats.cn"


def main() -> int:
    PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(PROFILE), headless=False, viewport={"width": 1100, "height": 800})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(BASE + "/login", wait_until="domcontentloaded")
        print("浏览器已弹出：请完成登录（邮箱+密码+验证码），成功后本窗口自动关闭并保存 cookie（最长 5 分钟）")
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                tok = page.evaluate("""async () => {
                  const r = await fetch('/api/user/current',{credentials:'include',cache:'no-store'});
                  if (r.status !== 200) return null;
                  const d = await r.json(); return d?.data?.token || null;
                }""")
            except Exception:
                tok = None
            if tok:
                cookies = {c["name"]: c["value"] for c in ctx.cookies([BASE]) if c["domain"].endswith("njstats.cn")}
                if "SESSION" in cookies:
                    OUT.write_text(json.dumps({"SESSION": cookies["SESSION"], "seeded_at": time.strftime("%F %T")}))
                    OUT.chmod(0o600)
                    print("✅ SESSION cookie 已保存 →", OUT)
                    ctx.close()
                    return 0
                print("登录成功但没抓到 SESSION cookie，重试…")
            time.sleep(2)
        print("超时未完成登录，未保存")
        ctx.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
