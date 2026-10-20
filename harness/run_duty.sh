#!/bin/zsh
# launchd 入口：DeepSeek agent 值守（缺 key 时降级为纯脚本，链路不断）
#
# 2026-10-21：加 mkdir 原子锁。此前本脚本与 scripts/njstats_duty.py 都没有锁，
# 手动跑一轮撞上 launchd 的 :05/:35 会让两个进程同时
#   - 读同一份 njstats_session.json / njstats_jwt
#   - 各跑一次 git pull --rebase --autostash 与 git push（njstats_duty.py 的重试循环）
#   - 同时写 data/state/challenge_state.json
# 实测撞过一次：08:35:03（手动）与 08:35:10（launchd）并存约 7 秒，当时侥幸没事。
# 用 mkdir 而不是 flock：macOS 自带 flock 只在部分版本有，mkdir 是 POSIX 原子的。
#
# 锁在 30 分钟后视为陈锁（进程被 kill -9 时不会自己清），避免永久卡死。
cd /Users/dykily/football-engine || exit 1

LOCK=${TMPDIR:-/tmp}/football-duty.lock
STALE_SEC=1800
if [[ -d "$LOCK" ]]; then
  AGE=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  if [[ $AGE -lt $STALE_SEC ]]; then
    echo "$(date '+%m-%d %H:%M') 已有 duty 在跑（锁龄 ${AGE}s），本轮跳过" >> ~/.football-engine/duty.log
    exit 0
  fi
  echo "$(date '+%m-%d %H:%M') 清理陈锁（${AGE}s）" >> ~/.football-engine/duty.log
  rmdir "$LOCK" 2>/dev/null
fi
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date '+%m-%d %H:%M') 抢锁失败（并发），本轮跳过" >> ~/.football-engine/duty.log
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

KEY=~/.football-engine/deepseek_key
if [[ -s $KEY ]] || [[ -n $DEEPSEEK_API_KEY ]]; then
  ./.venv/bin/python3 harness/deepseek_agent.py --duty
else
  echo "$(date '+%m-%d %H:%M') 无 DeepSeek key，降级跑确定性脚本" >> ~/.football-engine/duty.log
  ./.venv/bin/python3 scripts/njstats_duty.py
fi
