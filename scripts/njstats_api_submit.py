#!/usr/bin/env python3
"""NJStats 预测挑战 · 纯 API 提交客户端（2026-10-12 协议逆向）

协议（浏览器抓包差分实测）:
  POST https://www.njstats.cn/njserver/openApi/
  headers: Authorization: Bearer <localStorage.token JWT>
           Content-Type: application/x-protobuf, modId, msgId
  列表:    modId=1012 msgId=11  → 顶层 f5.f1[] 每项 {f1:比赛, f2:当前选择, f3:短id}
  提交:    modId=1012 msgId=2   body = {1:{1:短id, 2:市场码, 3:方向码}, 2:""}
           市场码 1=胜平负(方向1/2/3=主/平/客) 2=让球(方向1=主队让/2=客队受让)
           成功响应含 "suc"
安全边界: JWT 由本机浏览器 localStorage 维护（约 20h 过期）；本脚本只读用户
本人账号、只提交质量门槛筛选后的场次；API 失败(非 suc/401)→ 退出码非0，
由自动化回退 UI 点击流（_vault/njstats_challenge_submit.js）。

用法:
  njstats_api_submit.py list                  # 打印可预测列表 json（sid/队名/时间/已选）
  njstats_api_submit.py submit <sid> <mkt> <pick>
  njstats_api_submit.py check                 # JWT 存在性与列表可达性自检
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys
from pathlib import Path

import requests

API = "https://www.njstats.cn/njserver/openApi/"
JWT_FILE = Path.home() / ".football-engine" / "njstats_jwt"
PROXY = {"https": os.environ.get("NJSTATS_PROXY", "http://127.0.0.1:7897")}
if os.environ.get("NJSTATS_NOPROXY"):
    PROXY = {}


# ---------- 最小 protobuf wire 编解码（仅 varint / length-delimited） ----------

def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field(fn: int, payload: bytes) -> bytes:
    return _varint(fn << 3 | 2) + _varint(len(payload)) + payload


def _field_v(fn: int, n: int) -> bytes:
    return _varint(fn << 3) + _varint(n)


def encode_submit(sid: str, market: int, pick: int) -> bytes:
    inner = _field(1, sid.encode()) + _field_v(2, market) + _field_v(3, pick)
    return _field(1, inner) + _field(2, b"")


def decode_msg(buf: bytes):
    """返回 [(field_no, kind, value)]; kind: 'v'/'s'(bytes)。解析失败即返回已读部分。"""
    out = []
    i = 0
    while i < len(buf):
        try:
            key = 0; shift = 0
            b = buf[i]; i += 1
            while b & 0x80:
                key |= (b & 0x7F) << shift; shift += 7
                b = buf[i]; i += 1
            key |= b << shift
            fn, wt = key >> 3, key & 7
            if wt == 0:
                v = 0; shift = 0
                b = buf[i]; i += 1
                while b & 0x80:
                    v |= (b & 0x7F) << shift; shift += 7
                    b = buf[i]; i += 1
                v |= b << shift
                out.append((fn, 'v', v))
            elif wt == 2:
                ln = 0; shift = 0
                b = buf[i]; i += 1
                while b & 0x80:
                    ln |= (b & 0x7F) << shift; shift += 7
                    b = buf[i]; i += 1
                ln |= b << shift
                out.append((fn, 's', buf[i:i+ln])); i += ln
            elif wt == 5:
                out.append((fn, 'f32', buf[i:i+4])); i += 4
            elif wt == 1:
                out.append((fn, 'f64', buf[i:i+8])); i += 8
            else:
                break
        except (IndexError, ValueError):
            break
    return out


def _get(fields, fn, kind='s'):
    for f, k, v in fields:
        if f == fn and k == kind:
            return v
    return None


def _gets(fields, fn):
    return [v for f, k, v in fields if f == fn and k == 's']


# ---------- HTTP ----------

def _hdrs(mod_id: str, msg_id: str) -> dict:
    tok = JWT_FILE.read_text().strip() if JWT_FILE.exists() else ""
    if not tok:
        raise SystemExit("缺 JWT: 从浏览器 localStorage('token') 存入 ~/.football-engine/njstats_jwt")
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/x-protobuf",
            "modId": mod_id, "msgId": msg_id}


def _post(mod_id: str, msg_id: str, body: bytes, timeout: int = 20):
    r = requests.post(API, headers=_hdrs(mod_id, msg_id), data=body,
                      proxies=PROXY, timeout=timeout)
    return r.status_code, r.content


def cmd_list(page: int = 1, size: int = 60) -> int:
    body = _field(2, _field_v(1, page) + _field_v(2, size))
    st, raw = _post("1012", "11", body)
    if st != 200:
        print(f"列表 HTTP {st}", file=sys.stderr); return 2
    top = decode_msg(raw)
    container = _get(top, 5)
    if not container:
        print("列表结构异常", file=sys.stderr); return 2
    items = []
    for m in decode_msg(container):
        if m[0] != 1 or m[1] != 's':
            continue
        e = decode_msg(m[2])
        match = _get(e, 1)
        if not match:
            continue
        mf = decode_msg(match)
        def team(fn):
            t = _get(mf, fn)
            if not t:
                return None
            tf = decode_msg(t)
            s = _get(tf, 3)
            return s.decode('utf-8', 'ignore') if s else None
        sel = _get(e, 2)
        picked = None
        if sel:
            sf = decode_msg(sel)
            m1, p2, o3 = _get(sf, 1, 'v'), _get(sf, 2, 'v'), _get(sf, 3)
            line = _get(sf, 7)
            picked = {"market": m1, "pick": p2, "odds": o3.decode() if o3 else None,
                      "line": line.decode() if line else None}
        ks = _get(mf, 6, 'v') or _get(mf, 6)
        ts = None
        t6 = _get(mf, 6)
        if t6:
            try: ts = int(t6.decode())
            except Exception: pass
        items.append({"sid": (_get(e, 3) or b"").decode(), "home": team(3), "away": team(4),
                      "kick_ts": ts, "picked": bool(picked)})
    print(json.dumps(items, ensure_ascii=False))
    return 0


def cmd_submit(sid: str, mkt: str, pick: str) -> int:
    body = encode_submit(sid, int(mkt), int(pick))
    st, raw = _post("1012", "2", body)
    ok = st == 200 and b"suc" in raw
    print(json.dumps({"http": st, "ok": ok, "resp": base64.b64encode(raw[:40]).decode()},
                     ensure_ascii=False))
    return 0 if ok else 3


def cmd_check() -> int:
    st, raw = _post("1012", "11", b"")
    ok = st == 200 and b"suc" in raw[:200]
    print(json.dumps({"http": st, "list_ok": ok, "bytes": len(raw)}))
    return 0 if ok else 3


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "list":
        sys.exit(cmd_list())
    elif args and args[0] == "submit" and len(args) == 4:
        sys.exit(cmd_submit(args[1], args[2], args[3]))
    elif args and args[0] == "check":
        sys.exit(cmd_check())
    print(__doc__)
    sys.exit(1)
