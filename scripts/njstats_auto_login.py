#!/usr/bin/env python3
"""NJStats 全自动登录：解密图形/算术验证码 → RSA 登录 → 落盘 JWT。

逆向结论（2026-10-16）：
- 登录链 GET /api/public_key → GET /api/captcha → POST /api/login，全程同一 SESSION cookie。
- /api/captcha 返回三层 AES-CBC 密文，密钥硬编码在前端 captcha 模块：
    默认 key=hex 307d731886b5a4af0a76ba399019550f, iv=utf8 ivfzybeanext2023
    L1: 去首字符后 AES 解密 → L2: 再去前 3 字符 AES 解密 → {data,s,i}
    L3: data 用 s(hex) + i(utf8) AES 解密 → {image, index, type, params}
- 验证码两型：GRAPHIC 字母数字（提交 OCR 原文）；算术 "a op b = ?"（提交计算结果）。
- 密码用服务端 RSA 公钥 PKCS1v15 加密后 base64 提交。
- 凭据文件 ~/.football-engine/njstats_account：第一行账号，第二行密码（0600，仓库外）。

首选续期正道（无需本脚本、无验证码）：浏览器已登录 session 调 GET /api/user/current，
data.token 即全新 20h JWT。本脚本仅在浏览器 session 也死掉、需纯命令行重登时用；
且验证码 OCR（ddddocr）对算术型/叠字型不可靠，重登优先人工。

用法：njstats_auto_login.py [--push-secret] [--tries N]
成功→写 ~/.football-engine/njstats_jwt 并退 0；--push-secret 顺带把新 JWT 推到 GitHub secret。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import load_der_public_key

BASE = "https://www.njstats.cn"
KEY = bytes.fromhex("307d731886b5a4af0a76ba399019550f")
IV = b"ivfzybeanext2023"
HOME = Path.home()
ACCT_FILE = HOME / ".football-engine" / "njstats_account"
JWT_FILE = HOME / ".football-engine" / "njstats_jwt"
REPO = HOME / "football-engine"
PROXY = {"http": None, "https": None}  # 直连；requests 空 dict 会回落环境代理，必须显式 None
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def _aes_dec(b64: str, key: bytes, iv: bytes) -> bytes:
    raw = base64.b64decode(b64)
    if raw[:8] == b"Salted__":
        raw = raw[16:]
    d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    out = d.update(raw) + d.finalize()
    return out[: -out[-1]]  # PKCS7 unpad


def decrypt_captcha(text: str) -> dict:
    l1 = _aes_dec(text[1:], KEY, IV)
    l2 = _aes_dec(l1[3:].decode(), KEY, IV)
    outer = json.loads(l2)
    inner = _aes_dec(outer["data"], bytes.fromhex(outer["s"]), outer["i"].encode())
    return json.loads(inner)


def solve_arithmetic(ocr: str) -> str | None:
    """把 OCR 读到的表达式算出结果；非算术式返回 None。"""
    expr = ocr.replace("×", "*").replace("x", "*").replace("X", "*").replace("十", "+").replace("÷", "/")
    m = re.search(r"(\d+)\s*([+\-*/])\s*(\d+)", expr)
    if not m:
        return None
    a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
    r = {"+": a + b, "-": a - b, "*": a * b, "/": a / b if b else 0}[op]
    return str(int(r)) if float(r).is_integer() else str(r)


def solve(img: bytes, ocr) -> str:
    text = ocr.classification(img)
    calc = solve_arithmetic(text)
    if calc is not None and any(c in text for c in "×xX十+-*/÷="):
        return calc
    return text


def login_once(acct: str, pw: str, ocr, tries: int) -> str | None:
    from cryptography.hazmat.primitives.serialization import load_der_public_key
    s = requests.Session()
    s.proxies = PROXY
    hdr = {"User-Agent": UA, "Accept": "application/json", "Origin": BASE, "Referer": BASE + "/login"}
    der = base64.b64decode(s.get(BASE + "/api/public_key", headers=hdr, timeout=15).text.strip())
    rsa = load_der_public_key(der)
    for i in range(tries):
        try:
            cap = decrypt_captcha(s.get(f"{BASE}/api/captcha?v={i}", headers=hdr, timeout=15).text.strip())
            img = base64.b64decode(cap["image"].split(",")[-1])
            code = solve(img, ocr)
            enc = base64.b64encode(rsa.encrypt(pw.encode(), padding.PKCS1v15())).decode()
            r = s.post(BASE + "/api/login", headers={**hdr, "Content-Type": "application/json"},
                       json={"username": acct, "password": enc, "captcha": code,
                             "captchaIndex": cap.get("index"), "remember-me": True}, timeout=15)
            j = r.json()
            tok = (j.get("data") or {}).get("token") if isinstance(j.get("data"), dict) else None
            if tok:
                print(f"[auto-login] 第 {i+1} 次成功 code={code!r}")
                return tok
            print(f"[auto-login] 第 {i+1} 次失败 code={code!r} → {j.get('message', r.status_code)}")
        except Exception as e:  # noqa: BLE001
            print(f"[auto-login] 第 {i+1} 次异常：{e}")
    return None


def push_secret(jwt: str) -> None:
    try:
        from nacl.encoding import Base64Encoder
        from nacl.public import PublicKey, SealedBox
    except ImportError:
        print("[auto-login] 无 pynacl，跳过推 secret"); return
    cred = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                          capture_output=True, text=True, cwd=REPO).stdout
    tok = next((l.split("=", 1)[1] for l in cred.splitlines() if l.startswith("password=")), "")
    if not tok:
        print("[auto-login] 无 GitHub token，跳过推 secret"); return
    repo = os.environ.get("GITHUB_REPO", "wlrwx/football-engine")
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    pk = requests.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers=h, timeout=30, proxies=PROXY).json()
    sealed = SealedBox(PublicKey(pk["key"], Base64Encoder)).encrypt(jwt.encode())
    r = requests.put(f"https://api.github.com/repos/{repo}/actions/secrets/NJSTATS_JWT", headers=h,
                     json={"key_id": pk["key_id"], "encrypted_value": base64.b64encode(sealed).decode()},
                     timeout=30, proxies=PROXY)
    print(f"[auto-login] 推 secret → {r.status_code}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push-secret", action="store_true")
    ap.add_argument("--tries", type=int, default=12)
    a = ap.parse_args(argv)
    if not ACCT_FILE.exists():
        print(f"[auto-login] 缺凭据文件 {ACCT_FILE}"); return 2
    acct, pw = ACCT_FILE.read_text().split("\n")[:2]
    try:
        import ddddocr
        ocr = ddddocr.DdddOcr(show_ad=False)
    except Exception as e:  # noqa: BLE001
        print(f"[auto-login] ddddocr 不可用：{e}"); return 3
    tok = login_once(acct, pw, ocr, a.tries)
    if not tok:
        print("[auto-login] 全部尝试失败"); return 1
    JWT_FILE.write_text(tok)
    os.chmod(JWT_FILE, 0o600)
    exp = json.loads(base64.urlsafe_b64decode(tok.split(".")[1] + "==")).get("exp")
    print(f"[auto-login] JWT 已落盘 exp={exp}")
    if a.push_secret:
        push_secret(tok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
