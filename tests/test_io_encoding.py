"""文件 IO 与子进程必须显式指定 UTF-8（2026-10-28）。

背景：本机是 Windows，`Path.read_text()` / `open()` / `subprocess(text=True)`
不传 encoding 时走系统 locale（cp936）。历史上这造成两类真实故障：
  - 生产代码读自己写的 UTF-8 JSON → UnicodeDecodeError，被 except 吞掉后静默 0 行
    （lgbm_shadow 的训练集、DJYY 快照富化都踩过）；
  - 本地 Windows 值守写出的数据是 GBK，CI（Linux/UTF-8）读到就崩。

这个测试只做静态约束，不执行被测代码：新代码漏写 encoding 时在 PR 阶段就红，
而不是等到某台机器上偶发。允许的例外：
  - 二进制模式（open(..., "rb")）
  - 非内置的 .open()（os.open / zipfile.open / tarfile.open 等不是文本 API）
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("engine", "scripts", "evolution", "tests")
SUBPROCESS_ATTRS = {"run", "Popen", "check_output", "check_call", "call"}


def _calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def _attr(node: ast.Call) -> str | None:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return None


def _has_encoding(node: ast.Call) -> bool:
    return any(k.arg == "encoding" for k in node.keywords)


def _mode(node: ast.Call):
    for k in node.keywords:
        if k.arg == "mode" and isinstance(k.value, ast.Constant):
            return k.value.value
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        return node.args[1].value
    return None


def _violations() -> list[str]:
    out = []
    for d in SCAN_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            if "__pycache__" in p.parts or ".venv" in p.parts:
                continue
            rel = p.relative_to(ROOT)
            for node in _calls(p):
                a = _attr(node)
                if a in ("read_text", "write_text"):
                    if not node.args and not _has_encoding(node):
                        out.append(f"{rel}:{node.lineno} {a}() 缺 encoding=")
                elif a == "open" and isinstance(node.func, ast.Name):
                    mode = _mode(node)
                    if isinstance(mode, str) and "b" in mode:
                        continue
                    if not _has_encoding(node):
                        out.append(f"{rel}:{node.lineno} open() 缺 encoding=")
                elif (a in SUBPROCESS_ATTRS
                      and isinstance(node.func, ast.Attribute)
                      and isinstance(node.func.value, ast.Name)
                      and node.func.value.id == "subprocess"):
                    kws = {k.arg: k.value for k in node.keywords if k.arg}
                    text_mode = any(
                        isinstance(kws.get(k), ast.Constant) and kws[k].value is True
                        for k in ("text", "universal_newlines")
                    )
                    if text_mode and "encoding" not in kws:
                        out.append(f"{rel}:{node.lineno} subprocess text=True 缺 encoding=")
    return out


def test_all_text_io_pins_utf8():
    bad = _violations()
    assert not bad, "以下调用未指定编码（Windows 默认 cp936，会静默乱码/报错）：\n" + "\n".join(bad)
