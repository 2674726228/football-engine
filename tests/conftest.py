"""pytest 配置：把仓库根目录加入 sys.path，使 tests 可 import engine.*。"""
import sys
from pathlib import Path

# Windows 的 stdout 编码取决于系统 locale（本机 cp936、GitHub runner cp1252），
# 生产代码里的中文/符号 print 在测试进程内会直接 UnicodeEncodeError。
# 测试只关心"跑通"，统一用 UTF-8 + replace，不因控制台编码而假红。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
