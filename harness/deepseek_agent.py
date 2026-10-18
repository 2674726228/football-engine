#!/usr/bin/env python3
"""DeepSeek 驱动的最小 agent harness（OpenAI 兼容 tool-calling）。

用途：把"需要判断力"的值守/复盘类任务从 ZCode 会话迁到 DeepSeek API 上自主执行。
密钥：~/.football-engine/deepseek_key（0600）或环境变量 DEEPSEEK_API_KEY。

用法：
  ./.venv/bin/python3 harness/deepseek_agent.py "把 W42 挑战结算写成周报"
  ./.venv/bin/python3 harness/deepseek_agent.py --duty        # 跑值守简报任务
安全：bash 工具超时 180s、最多 25 轮、无 sudo/网络删除白名单——自用机器，自负其责。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = Path.home() / ".football-engine" / "deepseek_key"
API = "https://api.deepseek.com/chat/completions"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

DUTY_PROMPT = (ROOT / "harness" / "duty_prompt.md").read_text(encoding="utf-8") if (ROOT / "harness" / "duty_prompt.md").exists() else "执行 cd ~/football-engine && ./.venv/bin/python3 scripts/njstats_duty.py 并总结输出"

TOOLS = [{
    "type": "function",
    "function": {
        "name": "bash",
        "description": "在 ~/football-engine 下执行 shell 命令",
        "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]},
    },
}]


def run_bash(cmd: str) -> str:
    p = subprocess.run(["zsh", "-c", cmd], cwd=ROOT, capture_output=True, text=True, timeout=180, check=False)
    return (p.stdout + "\n" + p.stderr)[-4000:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", nargs="?", default="")
    ap.add_argument("--duty", action="store_true", help="使用值守任务提示")
    ap.add_argument("--chat", action="store_true", help="交互式对话模式（开个对话直接告诉它）")
    a = ap.parse_args()
    key = os.environ.get("DEEPSEEK_API_KEY") or (KEY_FILE.read_text().strip() if KEY_FILE.exists() else "")
    if not key:
        print("缺 DeepSeek key：写 ~/.football-engine/deepseek_key 或设 DEEPSEEK_API_KEY"); return 2
    task = DUTY_PROMPT if a.duty else a.task
    if not task:
        print("给个任务"); return 2
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    msgs = [{"role": "system", "content": "你是 football-engine 的运维 agent。工作目录 ~/football-engine。用 bash 工具完成任务，最后用中文简报结论。"}]

    def turn(user_text: str, budget: int = 25) -> bool:
        """一轮对话：喂用户输入，跑工具循环直到模型给出纯文本答复。返回 False=致命错误。"""
        msgs.append({"role": "user", "content": user_text})
        for _ in range(budget):
            body = {"model": MODEL, "messages": msgs, "tools": TOOLS, "tool_choice": "auto"}
            r = requests.post(API, headers=h, json=body, timeout=120, proxies={"http": None, "https": None})
            if r.status_code != 200:
                print("API", r.status_code, r.text[:200]); return False
            msg = r.json()["choices"][0]["message"]
            msgs.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                print(msg.get("content", ""))
                return True
            for c in calls:
                cmd = json.loads(c["function"]["arguments"])["cmd"]
                print(f"$ {cmd[:120]}")
                msgs.append({"role": "tool", "tool_call_id": c["id"], "content": run_bash(cmd)})
        print("达到轮次上限")
        return True

    if a.chat:
        print("DeepSeek agent 对话模式（ctrl-d 退出）")
        while True:
            try:
                line = input("\n你> ").strip()
            except EOFError:
                return 0
            if line:
                print("agent> ", end="", flush=True)
                if not turn(line):
                    return 1
        return 0
    ok = turn(task)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
