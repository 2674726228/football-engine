"""NJStats 脚本族的两处跨脚本一致性（2026-10-13）。

## 为什么值得测

同一个 key 在两处用了不同文件名：
- `njstats_archive.py` 找 `njstats_key`
- 挑战提交器 `njstats_challenge/submit.js` 找 `njstats_apikey.txt`

各自"只认自己那个名字"的后果不是报错，而是**静默跳过**：
归档脚本认为"无 key"直接 return 0，看起来跑过了实际一条没采。
这类错误的排查成本极高（要在"额度不够/网络不通/窗口不对"里排除掉一圈），
所以必须锁住。

代理也一样：写死 `127.0.0.1:7897` 的机器，不开 Clash 时每次请求
`RequestException` → 返回 None → 静默零产物。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def test_both_scripts_accept_same_key_filenames():
    """两个脚本必须认同一组文件名（含挑战提交器用的那个）。"""
    import njstats_archive as na
    import njstats_capture as nc

    for mod in (na, nc):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for name in ("njstats_key", "njstats_apikey.txt", "njstats_token.txt"):
            assert name in src, f"{Path(mod.__file__).name} 不认 key 文件名 {name}"


def test_load_key_resolves_real_file(monkeypatch, tmp_path):
    """有 key 文件时必须能取到值（不能静默返回空串）。"""
    import njstats_archive as na

    monkeypatch.delenv("NJSTATS_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    # 2026-10-23：load_key 用的是 Path.home()，Windows 上它读 USERPROFILE 而不是
    # HOME（POSIX 才读 HOME），所以只 patch 环境变量在 Windows 不生效——直接替换
    # Path.home 本身，两种平台行为一致。
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    d = tmp_path / ".football-engine"
    d.mkdir(parents=True)
    (d / "njstats_apikey.txt").write_text("TESTKEY123", encoding="utf-8")
    assert na.load_key() == "TESTKEY123"


def test_load_key_env_takes_priority(monkeypatch, tmp_path):
    """环境变量优先（CI 走 secrets 注入，不落盘）。"""
    import njstats_archive as na

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NJSTATS_TOKEN", "ENVKEY")
    d = tmp_path / ".football-engine"
    d.mkdir(parents=True)
    (d / "njstats_key").write_text("FILEKEY", encoding="utf-8")
    assert na.load_key() == "ENVKEY"


def test_load_key_empty_when_nothing(monkeypatch, tmp_path):
    """什么都没有时返回空串（调用方据此跳过，而不是用空 key 打请求）。"""
    import njstats_archive as na

    monkeypatch.delenv("NJSTATS_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert na.load_key() == ""


def test_load_key_skips_blank_files(monkeypatch, tmp_path):
    """空文件要跳过继续找下一个（不然一个空文件就能毒住整条链）。"""
    import njstats_archive as na

    monkeypatch.delenv("NJSTATS_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    # 同 test_load_key_resolves_real_file：Path.home() 在 Windows 上不认 HOME。
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    d = tmp_path / ".football-engine"
    d.mkdir(parents=True)
    (d / "njstats_key").write_text("", encoding="utf-8")
    (d / "njstats_apikey.txt").write_text("REALKEY", encoding="utf-8")
    assert na.load_key() == "REALKEY"


def test_proxy_is_configurable_not_hardcoded():
    """代理必须可配置（环境变量能覆盖），不能写死端口。"""
    import njstats_archive as na

    src = Path(na.__file__).read_text(encoding="utf-8")
    assert "NJSTATS_PROXY" in src, "缺少代理环境变量覆盖入口"
    assert "_resolve_proxy" in src, "缺少代理探测函数"


def test_proxy_env_override(monkeypatch):
    """NJSTATS_PROXY 设置时必须生效。"""
    import importlib

    import njstats_archive as na

    monkeypatch.setenv("NJSTATS_PROXY", "http://127.0.0.1:9999")
    importlib.reload(na)
    try:
        assert na.PROXY == {"https": "http://127.0.0.1:9999",
                            "http": "http://127.0.0.1:9999"}
    finally:
        monkeypatch.delenv("NJSTATS_PROXY", raising=False)
        importlib.reload(na)
