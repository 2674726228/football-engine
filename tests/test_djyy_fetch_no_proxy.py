"""DJYY 抓取必须绕开系统代理（2026-10-23）。

macOS 的 urllib 会自动读取系统代理设置（本机 127.0.0.1:7897）。duty 设
`DJYY_PROXY=""` 本意是住宅 IP 直连，但 urllib 仍走系统代理——2026-10-23 00:10
实测抓取进程连在 7897 上卡了 6+ 分钟。同一首页直连 1.6s、代理 3.9s。
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "scripts" / "fetch_djyy_ssr.py"


def _load():
    spec = importlib.util.spec_from_file_location("fetch_djyy_ssr_test", SPEC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_opener_has_no_proxy_handler():
    """空 ProxyHandler 的作用是抑制默认处理器，而不是被注册。

    build_opener 看到调用方传了 ProxyHandler 就跳过默认的那个；空 dict 的实例
    没有 *_open 方法，最终不会被 add_handler 注册——净效果就是「不代理」。
    一旦这里出现 ProxyHandler，说明系统代理（本机 7897）会重新生效。
    """
    mod = _load()
    proxies = [h for h in mod._OPENER.handlers
               if h.__class__.__name__ == "ProxyHandler"]
    assert proxies == [], f"系统代理会生效: {proxies}"


def test_fetch_uses_the_no_proxy_opener(monkeypatch):
    """fetch_json 不能再走 urllib.request.urlopen（它读系统代理）。"""
    mod = _load()
    called = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'

    def fake_open(req, timeout=None):
        called["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(mod._OPENER, "open", fake_open)
    assert mod._fetch_json("https://djyylive.com/api/x") == {"ok": True}
    assert "timeout" in called
