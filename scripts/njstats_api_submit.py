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
import sys
from pathlib import Path

import requests

API = "https://www.njstats.cn/njserver/openApi/"
JWT_FILE = Path.home() / ".football-engine" / "njstats_jwt"
# NJSTATS_NOPROXY=1（CI 端）：显式 None 屏蔽 runner 任何代理环境；Mac 默认走 Clash 7897
PROXY = {"http": None, "https": None} if os.environ.get("NJSTATS_NOPROXY") \
    else {"https": os.environ.get("NJSTATS_PROXY", "http://127.0.0.1:7897")}


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


def _hdrs_with(jwt: str, mod_id: str, msg_id: str) -> dict:
    return {"Authorization": f"Bearer {jwt}", "Content-Type": "application/x-protobuf",
            "modId": mod_id, "msgId": msg_id}


def _post_jwt(jwt: str, mod_id: str, msg_id: str, body: bytes):
    r = requests.post(API, headers=_hdrs_with(jwt, mod_id, msg_id), data=body,
                      proxies=PROXY, timeout=20)
    return r.status_code, r.content


def list_items(jwt: str | None = None, page: int = 1, size: int = 60):
    """可导入：[{sid,home,away,kick_ts}] 或 None。"""
    jwt = jwt or (JWT_FILE.read_text().strip() if JWT_FILE.exists() else "")
    if not jwt:
        return None
    body = _field(2, _field_v(1, page) + _field_v(2, size))
    st, raw = _post_jwt(jwt, "1012", "11", body)
    if st != 200:
        return None
    top = decode_msg(raw)
    container = _get(top, 5)
    if not container:
        return None
    out = []
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
            sb = _get(tf, 3)
            return sb.decode('utf-8', 'ignore') if sb else None
        ts = None
        t6 = _get(mf, 6)
        if t6:
            try:
                ts = int(t6.decode())
            except Exception:
                ts = None
        out.append({"sid": (_get(e, 3) or b"").decode(), "home": team(3),
                    "away": team(4), "kick_ts": ts})
    return out


def submit(jwt: str, sid: str, mkt: int, pick: int) -> bool:
    st, raw = _post_jwt(jwt, "1012", "2", encode_submit(sid, mkt, pick))
    return st == 200 and b"suc" in raw


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


def parse_board(raw: bytes) -> list[dict]:
    """msgId=1 全板响应 → [{sid,home,away,kick_ts}]（中文名在队子消息 f3）。纯函数可测。"""
    top = decode_msg(raw)
    cont = _get(top, 5)
    if not cont:
        return []
    out = []
    for m in decode_msg(cont):
        if m[0] != 1 or m[1] != 's':
            continue
        e = decode_msg(m[2])
        sid = (_get(e, 7) or b"").decode("utf-8", "ignore")
        f1b = _get(e, 1)
        if not sid or not f1b:
            continue
        f1 = decode_msg(f1b)
        kt = _get(f1, 6)
        try:
            ts = int(kt)
        except (TypeError, ValueError):
            continue
        def nm(fn):
            b = _get(f1, fn)
            if not b:
                return None
            tf = decode_msg(b)
            cn = (_get(tf, 3) or b"").decode("utf-8", "ignore")
            return cn or (_get(tf, 2) or b"").decode("utf-8", "ignore")
        h, a = nm(3), nm(4)
        if h and a:
            out.append({"sid": sid, "home": h, "away": a, "kick_ts": ts})
    return out


def board_items(jwt: str | None = None) -> list[dict] | None:
    """可提交全板（345 场级），替代已死掉的 msgId=11 周列表。"""
    jwt = jwt or (JWT_FILE.read_text().strip() if JWT_FILE.exists() else "")
    if not jwt:
        return None
    st, raw = _post_jwt(jwt, "1012", "1", _field(3, b""))
    if st != 200:
        return None
    return parse_board(raw)


def parse_my_predictions(raw: bytes) -> list[dict]:
    """msgId=11 响应 → 账号已预测列表 [{sid, home, away, market, dir, odds}]。

    纯函数，可测。字段（2026-10-21 与本地 state 双向对账后确认）：
      条目: f1=比赛{f2=联赛,f3=主队,f4=客队}, f2=选择, f3=短 id
      选择: f1=市场码, f2=方向码, f3=赔率串
    这是**服务端真值**：无效 JWT 返回空（实测 len=5 仅 "suc"），
    有效 JWT 返回本账号全部已预测场次，与本地 challenge_state 无关。
    用途：跨机器去重（CI 与本机是两个独立仓库副本，靠 git state 会漏）。
    """
    idx = raw.find(b"suc")
    body = raw[idx + 3:] if idx >= 0 else raw
    top = decode_msg(body)
    cont = _get(top, 5)
    if not cont:
        return []
    out = []
    for m in decode_msg(cont):
        if m[0] != 1 or m[1] != "s":
            continue
        e = decode_msg(m[2])
        sid = (_get(e, 3) or b"").decode("utf-8", "ignore")
        f1b = _get(e, 1)
        if not sid or not f1b:
            continue
        f1 = decode_msg(f1b)

        def nm(fn):
            b = _get(f1, fn)
            if not b:
                return ""
            tf = decode_msg(b)
            cn = _get(tf, 3)
            return (cn or _get(tf, 2) or b"").decode("utf-8", "ignore")

        sel = _get(e, 2)
        market = direction = odds = None
        if sel:
            s = decode_msg(sel)
            market = _get(s, 1, kind="v")
            direction = _get(s, 2, kind="v")
            ob = _get(s, 3)
            odds = ob.decode("utf-8", "ignore") if ob else None
        out.append({"sid": sid, "home": nm(3), "away": nm(4),
                    "market": market, "dir": direction, "odds": odds})
    return out


def my_predictions(jwt: str | None = None) -> list[dict] | None:
    """本账号已预测场次（服务端真值）。取不到返回 None。"""
    jwt = jwt or (JWT_FILE.read_text().strip() if JWT_FILE.exists() else "")
    if not jwt:
        return None
    body = _field(2, _field_v(1, 1) + _field_v(2, 60))
    st, raw = _post_jwt(jwt, "1012", "11", body)
    if st != 200:
        return None
    return parse_my_predictions(raw)
