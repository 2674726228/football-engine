#!/bin/zsh
# launchd 入口：DeepSeek agent 值守（缺 key 时降级为纯脚本，链路不断）
cd /Users/dykily/football-engine || exit 1
KEY=~/.football-engine/deepseek_key
if [[ -s $KEY ]] || [[ -n $DEEPSEEK_API_KEY ]]; then
  ./.venv/bin/python3 harness/deepseek_agent.py --duty
else
  echo "$(date '+%m-%d %H:%M') 无 DeepSeek key，降级跑确定性脚本" >> ~/.football-engine/duty.log
  ./.venv/bin/python3 scripts/njstats_duty.py
fi
